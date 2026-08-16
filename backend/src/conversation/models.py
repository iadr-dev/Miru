"""The conversation domain models: `Thread`, `Turn`, `Message` (a tree via `id`/`parent_id`),
plus the message block-content payload types.

These interoperate with, rather than duplicate, the existing runtime/adapter types:
`Message.content` blocks of type `tool_use`/`tool_result` carry the same normalized shapes as
`llm.base.ToolCall`/`ToolResultPart` (re-used field names, not re-invented), and a `Turn`'s
`usage` field is `events.usage.TurnUsage` — this module never defines a second usage shape.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from events.usage import TurnUsage
from llm.base import ToolCall, ToolResultPart, WebSearchSource


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TurnStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


class Framework(StrEnum):
    NONE = "none"
    SINGLE = "single"
    SUBAGENT = "subagent"
    TEAM = "team"


class MessageBlockType(StrEnum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    CAVEAT = "caveat"
    RICH_CARD = "rich_card"
    STEERING = "steering"
    BRANCH_SUMMARY = "branch_summary"
    COMPACTION = "compaction"
    ATTACHMENT = "attachment"
    WEB_SEARCH_SOURCES = "web_search_sources"


class TextBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


class ThinkingBlock(BaseModel):
    """`agent_id` attributes this reasoning chunk to the lane that produced it (`root` for a
    non-fanned-out `single`/`none` turn) — needed so a persisted `subagent`/`team` turn's process
    trace can be grouped by lane the same way the live SSE view already is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    agent_id: str = "root"


class ToolUseBlock(BaseModel):
    """`agent_id` — see `ThinkingBlock`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call: ToolCall
    activity: str = Field(min_length=1, description="the NL narration label, e.g. 'Checking...'")
    data_class: str = "public"
    agent_id: str = "root"


class ToolResultBlock(BaseModel):
    """`agent_id` — see `ThinkingBlock`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: ToolResultPart
    locked: bool = False
    agent_id: str = "root"


class CaveatBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    text: str


class RichCardBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    card_type: str
    data: dict[str, object] = Field(default_factory=dict)


class SteeringBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


class BranchSummaryBlock(BaseModel):
    """A structured decision+cause summary of an abandoned branch — never free prose, per the
    "carry the conclusion, drop the transcript" rule shared with `CompactionEntry`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approach: str
    outcome: str  # "tried" | "failed" | "confirmed"
    cause: str
    key_figures: tuple[str, ...] = Field(default_factory=tuple)
    source_ref: str | None = None


class CompactionBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str
    preserved_facts: tuple[str, ...] = Field(default_factory=tuple)
    preserved_caveats: tuple[str, ...] = Field(default_factory=tuple)


class AttachmentBlock(BaseModel):
    """Metadata + (for documents) extracted text for one user-uploaded chat attachment.

    Deliberately **never** carries the raw file/image bytes — those are ephemeral, living only
    in-memory for the one turn's LLM call (see `api/routers/agent.py`'s `AttachmentInput` and
    `attachments/extractor.py`), never persisted here past that turn, mirroring this codebase's
    "don't persist a raw blob where a smaller derived form suffices" discipline
    (`governance.md`'s no-raw-payload-logging principle, applied to user-uploaded content
    rather than model/source output). `extracted_text` is the `markitdown`-converted Markdown
    for a document attachment (`.docx`/`.xlsx`/`.pptx`/`.pdf`/...); it is `None` for an image
    attachment (images go to the LLM as vision input for that turn only, never through
    `markitdown`, and are not re-derivable as text here)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    is_image: bool
    extracted_text: str | None = None
    size_bytes: int = Field(ge=0)


class WebSearchSourcesBlock(BaseModel):
    """The sources a turn's native web search actually consulted, persisted so a reopened thread
    renders the same "N sources" pill the live stream showed — without this block, `web_search.
    result` was a live-only SSE event with nowhere to land once the turn finished (the bug this
    block exists to fix). `sources` reuses `llm.base.WebSearchSource` verbatim (same shape
    `EventBusWebSearchHook` already publishes over SSE) rather than re-declaring a second
    url/title record, per this module's own "interoperate, don't duplicate" convention."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[WebSearchSource, ...] = Field(default_factory=tuple)


MessageBlockContent = (
    TextBlock
    | ThinkingBlock
    | ToolUseBlock
    | ToolResultBlock
    | CaveatBlock
    | RichCardBlock
    | SteeringBlock
    | BranchSummaryBlock
    | CompactionBlock
    | AttachmentBlock
    | WebSearchSourcesBlock
)


class Message(BaseModel):
    """One node in a turn's message tree. `parent_id=None` marks a root message. Only the
    chain from the thread's `active_leaf_message_id` back to its root is ever sent to the
    LLM — see `conversation.store.ConversationStore.active_path`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    turn_id: str
    user_id: str
    parent_id: str | None
    seq: int = Field(ge=0)
    type: MessageBlockType
    content: MessageBlockContent
    created_at: datetime


class Turn(BaseModel):
    """One user request -> one agent response cycle. `messages` holds every block on this
    turn (already filtered to the active path by the store — a `Turn` itself never carries
    an abandoned branch)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    thread_id: str
    user_id: str
    role: TurnRole
    status: TurnStatus
    parent_turn_id: str | None = None
    regenerate_of: str | None = None
    framework: Framework | None = None
    idempotency_key: str | None = None
    created_at: datetime
    messages: tuple[Message, ...] = Field(default_factory=tuple)
    usage: TurnUsage | None = None


class Thread(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    title: str
    starred: bool = False
    archived: bool = False
    tags: tuple[str, ...] = Field(default_factory=tuple)
    model_override: str | None = None
    personality_override: str | None = None
    active_leaf_message_id: str | None = None
    draft_content: str | None = None
    last_message_at: datetime
    version: int = 0
    created_at: datetime
    updated_at: datetime
    turns: tuple[Turn, ...] = Field(default_factory=tuple)


__all__ = [
    "AttachmentBlock",
    "BranchSummaryBlock",
    "CaveatBlock",
    "CompactionBlock",
    "Framework",
    "Message",
    "MessageBlockContent",
    "MessageBlockType",
    "RichCardBlock",
    "SteeringBlock",
    "TextBlock",
    "ThinkingBlock",
    "Thread",
    "ToolResultBlock",
    "ToolUseBlock",
    "Turn",
    "TurnRole",
    "TurnStatus",
    "WebSearchSourcesBlock",
]
