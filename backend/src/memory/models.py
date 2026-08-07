"""Wire-level shapes for long-term memory: the typed `Memory` record and the governed
write-path validation that rejects credentials, raw source payloads, or trading
recommendations before they ever reach the store.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


# Keyword/pattern heuristics — a deliberate first pass (documented precedent:
# `agent/guardrails.py` uses the same style of keyword/pattern matching for the identical
# reason: cheap, auditable, and good enough to catch the obvious cases without an LLM call on
# every memory write).
_CREDENTIAL_KEY_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "ciphertext",
    "private_key",
)
_RAW_PAYLOAD_KEY_MARKERS: Final[tuple[str, ...]] = ("raw_payload", "raw_response", "source_payload")
_TRADING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\byou should (buy|sell|hold)\b",
        r"\b(buy|sell|hold)\s+(rating|signal|recommendation|call)\b",
        r"\bprice target\b",
        r"\btarget price\b",
        r"\bposition siz(e|ing)\b",
        r"目標價",
        r"買進建議",
        r"賣出建議",
    )
)


def scan_content_for_governance_violations(content: dict[str, object]) -> str | None:
    """Return a human-readable reason if `content` violates a governance rule, else `None`.
    Checks key names (credentials/raw payloads are conventionally under a recognizable key)
    and string values (trading language can appear in free text regardless of key name)."""
    for key, value in content.items():
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _CREDENTIAL_KEY_MARKERS):
            return f"content key {key!r} looks like a credential — never stored in memory"
        if any(marker in lowered_key for marker in _RAW_PAYLOAD_KEY_MARKERS):
            return f"content key {key!r} looks like a raw source payload — never stored verbatim"
        if isinstance(value, str) and any(pattern.search(value) for pattern in _TRADING_PATTERNS):
            return f"content key {key!r} contains trading-recommendation language"
    return None


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
    "Memory",
    "MemoryGovernanceError",
    "MemoryScope",
    "MemoryType",
    "scan_content_for_governance_violations",
]
