"""`ConversationStore` — the SQLAlchemy-backed persistence layer for threads/turns/messages.

The one invariant every method here is written to protect: **only the active path is ever
materialized for the LLM.** `active_path(thread_id)` walks `messages.parent_id` from the
thread's `active_leaf_message_id` back to the root and returns that chain only — `switch_branch`
changes which leaf is active (an O(1) pointer flip) rather than copying or re-inflating any
messages, and `fork_thread` copies no messages at all (a fresh thread whose first turn holds a
`BranchSummaryBlock` of the source path, not the source path itself).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from conversation.models import (
    AttachmentBlock,
    BranchSummaryBlock,
    CaveatBlock,
    CompactionBlock,
    Framework,
    Message,
    MessageBlockContent,
    MessageBlockType,
    RichCardBlock,
    SteeringBlock,
    TextBlock,
    ThinkingBlock,
    Thread,
    ToolResultBlock,
    ToolUseBlock,
    Turn,
    TurnMode,
    TurnRole,
    TurnStatus,
    WebSearchSourcesBlock,
)
from db.models import MessageRow, ThreadRow, TurnRow
from events.usage import TurnUsage

logger = structlog.get_logger(__name__)


class ThreadNotFoundError(LookupError):
    pass


class TurnNotFoundError(LookupError):
    pass


class MessageNotFoundError(LookupError):
    pass


class StaleThreadVersionError(RuntimeError):
    """A thread mutation carried an `If-Match` version that no longer matches the stored row —
    another device edited it first. The caller surfaces this as a 409 so the client refetches
    and retries rather than clobbering the newer state (multi-device optimistic concurrency)."""


@runtime_checkable
class ConversationStore(Protocol):
    async def create_thread(self, *, user_id: str, title: str = "") -> Thread: ...

    async def list_threads(
        self, *, user_id: str, include_archived: bool = False
    ) -> list[Thread]: ...

    async def get_thread(self, *, user_id: str, thread_id: str) -> Thread: ...

    async def append_turn(  # noqa: PLR0913 — one field per persisted dimension of a turn
        self,
        *,
        user_id: str,
        thread_id: str,
        role: TurnRole,
        blocks: list[tuple[MessageBlockType, MessageBlockContent]],
        parent_message_id: str | None,
        framework: Framework | None = None,
        idempotency_key: str | None = None,
        regenerate_of: str | None = None,
        turn_id: str | None = None,
        mode: TurnMode | None = None,
        usage: TurnUsage | None = None,
    ) -> Turn: ...

    async def active_path(self, *, thread_id: str) -> list[Message]: ...

    async def switch_branch(self, *, thread_id: str, target_message_id: str) -> None: ...

    async def fork_thread(
        self, *, user_id: str, source_thread_id: str, from_turn_id: str, title: str = ""
    ) -> Thread: ...

    async def delete_thread(self, *, user_id: str, thread_id: str) -> None: ...

    async def update_thread(  # noqa: PLR0913 — one field per independently-settable thread attr
        self,
        *,
        user_id: str,
        thread_id: str,
        title: str | None = None,
        starred: bool | None = None,
        archived: bool | None = None,
        tags: tuple[str, ...] | None = None,
        model_override: str | None = None,
        personality_override: str | None = None,
        clear_personality_override: bool = False,
        expected_version: int | None = None,
    ) -> Thread: ...

    async def get_turn(self, *, user_id: str, thread_id: str, turn_id: str) -> Turn: ...

    async def get_message(self, *, user_id: str, message_id: str) -> Message: ...

    async def search_threads(
        self, *, user_id: str, query: str, limit: int = 50
    ) -> list[Thread]: ...


def _message_row_to_model(row: MessageRow) -> Message:
    block_type = MessageBlockType(row.type)
    content = _content_model_for_type(block_type).model_validate(row.content)
    return Message(
        id=row.id,
        turn_id=row.turn_id,
        user_id=row.user_id,
        parent_id=row.parent_id,
        seq=row.seq,
        type=block_type,
        content=content,
        created_at=row.created_at,
    )


def _content_model_for_type(block_type: MessageBlockType) -> type[MessageBlockContent]:
    mapping: dict[MessageBlockType, type[MessageBlockContent]] = {
        MessageBlockType.TEXT: TextBlock,
        MessageBlockType.THINKING: ThinkingBlock,
        MessageBlockType.TOOL_USE: ToolUseBlock,
        MessageBlockType.TOOL_RESULT: ToolResultBlock,
        MessageBlockType.CAVEAT: CaveatBlock,
        MessageBlockType.RICH_CARD: RichCardBlock,
        MessageBlockType.STEERING: SteeringBlock,
        MessageBlockType.BRANCH_SUMMARY: BranchSummaryBlock,
        MessageBlockType.COMPACTION: CompactionBlock,
        MessageBlockType.ATTACHMENT: AttachmentBlock,
        MessageBlockType.WEB_SEARCH_SOURCES: WebSearchSourcesBlock,
    }
    return mapping[block_type]


def _stored_usage(row: TurnRow) -> TurnUsage | None:
    """The persisted usage snapshot, or None if absent / written by an incompatible version."""
    if not row.usage:
        return None
    try:
        return TurnUsage.model_validate(row.usage)
    except ValueError:
        return None


def _turn_row_to_model(row: TurnRow, messages: list[Message]) -> Turn:
    return Turn(
        id=row.id,
        thread_id=row.thread_id,
        user_id=row.user_id,
        role=TurnRole(row.role),
        status=TurnStatus(row.status),
        parent_turn_id=row.parent_turn_id,
        regenerate_of=row.regenerate_of,
        framework=Framework(row.framework) if row.framework else None,
        mode="deep_research" if row.mode == "deep_research" else None,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        messages=tuple(messages),
        usage=_stored_usage(row),
    )


def _thread_row_to_model(row: ThreadRow, turns: list[Turn]) -> Thread:
    return Thread(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        starred=row.starred,
        archived=row.archived,
        tags=tuple(row.tags),
        model_override=row.model_override,
        personality_override=row.personality_override,
        active_leaf_message_id=row.active_leaf_message_id,
        draft_content=row.draft_content,
        last_message_at=row.last_message_at,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        turns=tuple(turns),
    )


class SqlAlchemyConversationStore:
    """`ConversationStore` over one `AsyncSession` — same per-request-session convention as
    `memory.store.SqlAlchemyMemoryStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_thread(self, *, user_id: str, title: str = "") -> Thread:
        now = datetime.now(UTC)
        row = ThreadRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title,
            tags=[],
            last_message_at=now,
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("thread_created", user_id=user_id, thread_id=row.id)
        return _thread_row_to_model(row, [])

    async def list_threads(self, *, user_id: str, include_archived: bool = False) -> list[Thread]:
        stmt = select(ThreadRow).where(ThreadRow.user_id == user_id, ThreadRow.deleted_at.is_(None))
        if not include_archived:
            stmt = stmt.where(ThreadRow.archived.is_(False))
        stmt = stmt.order_by(ThreadRow.last_message_at.desc())
        result = await self._session.execute(stmt)
        return [_thread_row_to_model(row, []) for row in result.scalars().all()]

    async def _get_thread_row(
        self, *, user_id: str, thread_id: str, lock: bool = False
    ) -> ThreadRow:
        """The user's thread row.

        `lock=True` takes a row lock (`SELECT … FOR UPDATE`, a no-op on SQLite) for the edit
        paths, so a second concurrent edit waits and then sees the fresh `version`; without
        it two editors could both pass the version check and the last write would silently
        win."""
        statement = select(ThreadRow).where(ThreadRow.id == thread_id, ThreadRow.user_id == user_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            raise ThreadNotFoundError(thread_id)
        return row

    async def get_thread(self, *, user_id: str, thread_id: str) -> Thread:
        thread_row = await self._get_thread_row(user_id=user_id, thread_id=thread_id)
        turns_result = await self._session.execute(
            select(TurnRow).where(TurnRow.thread_id == thread_id).order_by(TurnRow.created_at)
        )
        turn_rows = turns_result.scalars().all()
        turns: list[Turn] = []
        for turn_row in turn_rows:
            messages_result = await self._session.execute(
                select(MessageRow).where(MessageRow.turn_id == turn_row.id).order_by(MessageRow.seq)
            )
            messages = [_message_row_to_model(row) for row in messages_result.scalars().all()]
            turns.append(_turn_row_to_model(turn_row, messages))
        return _thread_row_to_model(thread_row, turns)

    async def append_turn(  # noqa: PLR0913 — matches the Protocol's own shape above
        self,
        *,
        user_id: str,
        thread_id: str,
        role: TurnRole,
        blocks: list[tuple[MessageBlockType, MessageBlockContent]],
        parent_message_id: str | None,
        framework: Framework | None = None,
        idempotency_key: str | None = None,
        regenerate_of: str | None = None,
        turn_id: str | None = None,
        mode: TurnMode | None = None,
        usage: TurnUsage | None = None,
    ) -> Turn:
        thread_row = await self._get_thread_row(user_id=user_id, thread_id=thread_id)
        now = datetime.now(UTC)
        turn_row = TurnRow(
            id=turn_id or str(uuid.uuid4()),
            thread_id=thread_id,
            user_id=user_id,
            role=role.value,
            status=TurnStatus.DONE.value,
            framework=framework.value if framework else None,
            mode=mode,
            usage=usage.model_dump(mode="json") if usage is not None else None,
            idempotency_key=idempotency_key,
            regenerate_of=regenerate_of,
            created_at=now,
        )
        self._session.add(turn_row)
        await self._session.flush()

        messages: list[Message] = []
        parent_id = parent_message_id
        for seq, (block_type, content) in enumerate(blocks):
            message_row = MessageRow(
                id=str(uuid.uuid4()),
                turn_id=turn_row.id,
                user_id=user_id,
                parent_id=parent_id,
                seq=seq,
                type=block_type.value,
                content=content.model_dump(mode="json"),
                created_at=now,
            )
            self._session.add(message_row)
            await self._session.flush()
            messages.append(_message_row_to_model(message_row))
            parent_id = message_row.id

        thread_row.active_leaf_message_id = parent_id
        thread_row.last_message_at = now
        thread_row.updated_at = now
        await self._session.flush()
        logger.info("turn_appended", user_id=user_id, thread_id=thread_id, turn_id=turn_row.id)
        return _turn_row_to_model(turn_row, messages)

    async def active_path(self, *, thread_id: str) -> list[Message]:
        """Walk `parent_id` from the thread's active leaf back to the root — never the whole
        tree. Returns oldest-first, ready to convert straight to `llm.base.Message`s."""
        thread_result = await self._session.execute(
            select(ThreadRow).where(ThreadRow.id == thread_id)
        )
        thread_row = thread_result.scalar_one_or_none()
        if thread_row is None or thread_row.active_leaf_message_id is None:
            return []

        chain: list[Message] = []
        current_id: str | None = thread_row.active_leaf_message_id
        while current_id is not None:
            message_result = await self._session.execute(
                select(MessageRow).where(MessageRow.id == current_id)
            )
            message_row = message_result.scalar_one_or_none()
            if message_row is None:
                break
            chain.append(_message_row_to_model(message_row))
            current_id = message_row.parent_id
        chain.reverse()
        return chain

    async def switch_branch(self, *, thread_id: str, target_message_id: str) -> None:
        """Point the active leaf at `target_message_id` — an O(1) pointer flip. The caller
        (`conversation.branch_summary`) is responsible for computing and appending the
        `BranchSummaryEntry` for the branch being left *before* calling this, since this
        method has no way to know which messages become unreachable without walking the
        tree itself, and doing that here would duplicate `branch_summary`'s own traversal."""
        result = await self._session.execute(select(ThreadRow).where(ThreadRow.id == thread_id))
        thread_row = result.scalar_one()
        thread_row.active_leaf_message_id = target_message_id
        thread_row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def fork_thread(
        self, *, user_id: str, source_thread_id: str, from_turn_id: str, title: str = ""
    ) -> Thread:
        """A new thread that shares no message rows with the source — never a deep copy.
        The caller (`conversation.branch_summary`) is expected to seed the new thread's first
        turn with a summary of the source path; this method only creates the empty shell."""
        del from_turn_id  # accepted for the Protocol; the caller seeds the branch point itself
        await self._get_thread_row(user_id=user_id, thread_id=source_thread_id)
        new_title = title or f"Fork of {source_thread_id}"
        return await self.create_thread(user_id=user_id, title=new_title)

    async def delete_thread(self, *, user_id: str, thread_id: str) -> None:
        thread_row = await self._get_thread_row(user_id=user_id, thread_id=thread_id)
        thread_row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("thread_deleted", user_id=user_id, thread_id=thread_id)

    async def update_thread(  # noqa: PLR0913 — matches the Protocol's own shape above
        self,
        *,
        user_id: str,
        thread_id: str,
        title: str | None = None,
        starred: bool | None = None,
        archived: bool | None = None,
        tags: tuple[str, ...] | None = None,
        model_override: str | None = None,
        personality_override: str | None = None,
        clear_personality_override: bool = False,
        expected_version: int | None = None,
    ) -> Thread:
        """Patch thread metadata (rename/star/archive/tag/model-override). `expected_version`
        enforces optimistic concurrency: a mismatch means another device won the race — raise
        rather than overwrite the newer state. Every applied patch bumps `version`, so the next
        stale write is caught.

        `model_override` follows every other field's own "set if provided, never explicitly
        clear" semantics (`None` means the caller didn't touch this field, exactly like `title`/
        `starred`/`archived`/`tags` above) rather than adding a separate sentinel — there is no
        existing precedent in this method for distinguishing "omitted" from "clear to None", and
        this PATCH surface has no need to invent one yet: a client that wants to remove an
        override can PATCH a resolvable `"provider/model"` string back to the default, and a
        dedicated clear affordance can be added later if a real need for it shows up.
        """
        row = await self._get_thread_row(user_id=user_id, thread_id=thread_id, lock=True)
        if expected_version is not None and row.version != expected_version:
            raise StaleThreadVersionError(thread_id)
        if title is not None:
            row.title = title
        if starred is not None:
            row.starred = starred
        if archived is not None:
            row.archived = archived
        if tags is not None:
            row.tags = list(tags)
        if model_override is not None:
            row.model_override = model_override
        if clear_personality_override:
            row.personality_override = None  # back to following the account's setting
        elif personality_override is not None:
            row.personality_override = personality_override
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("thread_updated", user_id=user_id, thread_id=thread_id)
        return _thread_row_to_model(row, [])

    async def get_turn(self, *, user_id: str, thread_id: str, turn_id: str) -> Turn:
        """One turn + its messages, scoped to the owning user and thread."""
        await self._get_thread_row(user_id=user_id, thread_id=thread_id)
        result = await self._session.execute(
            select(TurnRow).where(
                TurnRow.id == turn_id,
                TurnRow.thread_id == thread_id,
                TurnRow.user_id == user_id,
            )
        )
        turn_row = result.scalar_one_or_none()
        if turn_row is None:
            raise TurnNotFoundError(turn_id)
        messages_result = await self._session.execute(
            select(MessageRow).where(MessageRow.turn_id == turn_id).order_by(MessageRow.seq)
        )
        messages = [_message_row_to_model(row) for row in messages_result.scalars().all()]
        return _turn_row_to_model(turn_row, messages)

    async def get_message(self, *, user_id: str, message_id: str) -> Message:
        """One message row, scoped to the owning user — used by `regenerate`/`edit` to load the
        original user-turn text without walking the whole thread."""
        result = await self._session.execute(
            select(MessageRow).where(MessageRow.id == message_id, MessageRow.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise MessageNotFoundError(message_id)
        return _message_row_to_model(row)

    async def search_threads(self, *, user_id: str, query: str, limit: int = 50) -> list[Thread]:
        """Case-insensitive substring search over a user's own thread titles and message text.
        Portable across SQLite/Postgres (`LIKE lower(...)` rather than a dialect-specific FTS
        index) — good enough for the top-bar palette; a real FTS index is a later optimization."""
        pattern = f"%{query.lower()}%"
        title_match = select(ThreadRow.id).where(
            ThreadRow.user_id == user_id,
            ThreadRow.deleted_at.is_(None),
            func.lower(ThreadRow.title).like(pattern),
        )
        message_match = (
            select(TurnRow.thread_id)
            .join(MessageRow, MessageRow.turn_id == TurnRow.id)
            .where(
                MessageRow.user_id == user_id,
                func.lower(func.cast(MessageRow.content, String)).like(pattern),
            )
        )
        result = await self._session.execute(
            select(ThreadRow)
            .where(
                ThreadRow.user_id == user_id,
                ThreadRow.deleted_at.is_(None),
                or_(ThreadRow.id.in_(title_match), ThreadRow.id.in_(message_match)),
            )
            .order_by(ThreadRow.last_message_at.desc())
            .limit(limit)
        )
        return [_thread_row_to_model(row, []) for row in result.scalars().all()]


__all__ = [
    "ConversationStore",
    "MessageNotFoundError",
    "SqlAlchemyConversationStore",
    "StaleThreadVersionError",
    "ThreadNotFoundError",
    "TurnNotFoundError",
]
