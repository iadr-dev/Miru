"""Wire-level shapes for long-term memory: the typed `Memory` record and the governed
write-path validation that rejects credentials, raw source payloads, or trading
recommendations before they ever reach the store.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.memory_rules import (
    STYLE_DIRECTIVE_MESSAGE,
    scan_content_for_governance_violations,
    scan_preference_for_style_directive,
)


class MemoryScope(StrEnum):
    USER = "user"
    THREAD = "thread"


class MemoryType(StrEnum):
    """`WATCHLIST` is retained only so legacy rows written before the relational watchlist
    store existed still validate — `memory.store.MemoryStore.upsert` refuses to write a new
    one (see that module's docstring)."""

    WATCHLIST = "watchlist"
    PREFERENCE = "preference"
    FINDING = "finding"
    ENTITY_NOTE = "entity_note"


class MemoryGovernanceError(ValueError):
    """Raised by `Memory`'s own validation when content looks like a credential, a raw
    source payload, or a trading recommendation — never silently dropped, never silently
    written; the caller sees exactly why the write was refused."""


class Memory(BaseModel):
    """One long-term memory record — `memories` row shape.

    `type=MemoryType.WATCHLIST` may only appear when constructing a `Memory` from an existing
    legacy row (`memory.store.MemoryStore` never sets it on a new write); this model itself
    does not enforce that distinction — see `memory/store.py`'s `upsert` for the write-path
    gate — because a *read* path (listing legacy rows) legitimately needs to construct one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    scope: MemoryScope
    thread_id: str | None = None
    type: MemoryType
    key: str = Field(min_length=1)
    content: dict[str, object] = Field(default_factory=dict)
    source_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    use_count: int = 0

    @model_validator(mode="after")
    def _scope_thread_consistency(self) -> Memory:
        if self.scope is MemoryScope.THREAD and self.thread_id is None:
            raise MemoryGovernanceError("scope='thread' requires a thread_id")
        return self

    @model_validator(mode="after")
    def _content_governance_scan(self) -> Memory:
        violation = scan_content_for_governance_violations(self.content)
        if violation is not None:
            raise MemoryGovernanceError(violation)
        return self


__all__ = [
    "STYLE_DIRECTIVE_MESSAGE",
    "Memory",
    "MemoryGovernanceError",
    "MemoryScope",
    "MemoryType",
    "scan_content_for_governance_violations",
    "scan_preference_for_style_directive",
]
