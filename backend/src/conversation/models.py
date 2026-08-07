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
from llm.base import ToolCall, ToolResultPart


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


class TextBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


class ThinkingBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


class ToolUseBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call: ToolCall
    activity: str = Field(min_length=1, description="the NL narration label, e.g. 'Checking...'")
    data_class: str = "public"


class ToolResultBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: ToolResultPart
    locked: bool = False


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
]
