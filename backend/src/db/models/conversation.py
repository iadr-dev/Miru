"""Conversation tables: threads, turns, messages, the SSE replay log, turn feedback."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.models._common import CASCADE_DELETE_ORPHAN as _CASCADE_DELETE_ORPHAN
from db.models._common import TURNS_ID_FK as _TURNS_ID_FK
from db.models._common import UTC_TIMESTAMP as _UTC

_SET_NULL = "SET NULL"


class ThreadRow(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(default="")
    starred: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # JSON array — portability
    model_override: Mapped[str | None] = mapped_column(default=None)
    personality_override: Mapped[str | None] = mapped_column(default=None)
    active_leaf_message_id: Mapped[str | None] = mapped_column(default=None)
    draft_content: Mapped[str | None] = mapped_column(default=None)
    last_message_at: Mapped[datetime] = mapped_column(_UTC)
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)
    deleted_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    turns: Mapped[list[TurnRow]] = relationship(
        back_populates="thread", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        Index("ix_threads_user_last_message", "user_id", "last_message_at"),
        Index("ix_threads_user_created", "user_id", "created_at"),
    )


class TurnRow(Base):
    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)  # denormalized for isolation queries
    role: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")
    parent_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey(_TURNS_ID_FK, ondelete=_SET_NULL), default=None
    )
    regenerate_of: Mapped[str | None] = mapped_column(
        ForeignKey(_TURNS_ID_FK, ondelete=_SET_NULL), default=None
    )
    framework: Mapped[str | None] = mapped_column(default=None)
    # How the request was made when it is not a plain chat ("deep_research"); regenerate/edit read
    # it off the user turn so a retry re-runs the same way. App-validated, nullable, additive.
    mode: Mapped[str | None] = mapped_column(default=None)
    # The finished turn's `TurnUsage` snapshot (tokens, per-model split) so a reopened thread can
    # show the same token line the live stream did. Nullable + additive.
    usage: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    thread: Mapped[ThreadRow] = relationship(back_populates="turns")
    messages: Mapped[list[MessageRow]] = relationship(
        back_populates="turn", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_turns_role"),
        CheckConstraint("status IN ('running','done','cancelled','error')", name="ck_turns_status"),
        CheckConstraint(
            "framework IS NULL OR framework IN ('none','single','subagent','team')",
            name="ck_turns_framework",
        ),
        Index("ix_turns_thread_created", "thread_id", "created_at"),
        Index("ix_turns_user_created", "user_id", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_turns_user_idempotency_key"),
        # At most one 'running' turn per thread — a partial-unique index, expressed with the
        # dialect-specific `postgresql_where`/`sqlite_where` kwargs `Index(...)` accepts directly
        # (no separate migration DDL needed for this).
        Index(
            "uq_turns_one_running_per_thread",
            "thread_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey(_TURNS_ID_FK, ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)  # denormalized for isolation + FTS
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete=_SET_NULL), default=None
    )
    seq: Mapped[int]
    type: Mapped[str]
    content: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    turn: Mapped[TurnRow] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "type IN ('text','thinking','tool_use','tool_result','caveat','rich_card',"
            "'steering','branch_summary','compaction','attachment','web_search_sources')",
            name="ck_messages_type",
        ),
        UniqueConstraint("turn_id", "seq", name="uq_messages_turn_seq"),
        Index("ix_messages_parent", "parent_id"),
    )


class StreamEventRow(Base):
    """SSE reconnect-replay log — see `events/bus.py`'s docstring for the live-fanout vs
    durable-replay split this table is the durable half of."""

    __tablename__ = "stream_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    # The streamed turn's id, not a foreign key: a regenerated answer streams under a fresh id
    # whose turn row only exists once the answer is saved, so a FK would reject every event of it.
    # The row belongs to its thread instead, which is what deletes it with the conversation.
    turn_id: Mapped[str]
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    event_seq: Mapped[int]
    event_type: Mapped[str]
    agent_id: Mapped[str | None] = mapped_column(default=None)
    parent_agent_id: Mapped[str | None] = mapped_column(default=None)
    node_id: Mapped[str | None] = mapped_column(default=None)
    data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        UniqueConstraint("turn_id", "event_seq", name="uq_stream_events_turn_seq"),
        Index("ix_stream_events_user_created", "user_id", "created_at"),
    )


class TurnFeedbackRow(Base):
    """One user's 👍/👎 (+ optional short comment) on a turn. `UNIQUE(turn_id, user_id)` is the
    idempotent-resubmit guard: tapping thumbs-down then thumbs-up on the same turn updates this
    one row (`SqlAlchemyFeedbackStore.submit` upserts on that key) rather than accumulating
    duplicate rows. This is deliberately just a rating capture — no rolling quality score, no
    alert threshold, no admin endpoints; that pipeline is a dedicated follow-up, not built here.
    """

    __tablename__ = "turn_feedback"

    id: Mapped[str] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey(_TURNS_ID_FK, ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    rating: Mapped[str]
    comment: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("rating IN ('up','down')", name="ck_turn_feedback_rating"),
        CheckConstraint(
            "comment IS NULL OR length(comment) <= 500", name="ck_turn_feedback_comment_len"
        ),
        UniqueConstraint("turn_id", "user_id", name="uq_turn_feedback_turn_user"),
    )
