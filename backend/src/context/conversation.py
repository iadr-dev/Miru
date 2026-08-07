"""Conversation (M5N, Mode C) — `context/conversation.py`.

The **AI-safe caveated artifact**: what the agent reasons over and what gets handed to any
external chat. Hard governance rules enforced here:

- **No raw payloads.** `ConversationFact` never carries `SourceResult.payload` (or any of its
  keys/values) verbatim — only a small, derived, human-readable `headline` plus normalized
  provenance fields (`source_id`, `data_class`, `timing_class`). `tests/context/
  test_conversation.py` asserts no raw payload blob/key ever appears in a built package.
- **Every fact keeps its currentness caveat.** `currentness_caveat` is copied onto every
  `ConversationFact` and is never summarized away, even under later compaction
  (`agent/context_window.py`, not yet built).
- **Non-trading framing.** This module makes no attempt at recommendation language; it
  reports *that* data exists and *how current* it is, nothing prescriptive.

Built from a `CanonicalPackage` and/or an `ObservationRun` — never straight from the router
(that would risk exposing raw payloads before the caveat/redaction step below runs).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from context.canonical import CanonicalPackage
from context.observation import ObservationRun
from schemas.enums import Capability, DataClass, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult

# Known `row_key` conventions used by the adapters built so far (sources/finmind.py,
# sources/twse_openapi.py, sources/yfinance.py) — used only to count records for a
# human-readable headline. Unknown/future shapes fall back to a generic "data available"
# headline rather than guessing at structure (never introspects raw values, only row counts).
_KNOWN_LIST_KEYS: tuple[str, ...] = ("bars", "lines", "events", "rows")
_KNOWN_SINGLE_KEYS: tuple[str, ...] = ("bar", "consensus", "esg", "classification")


class ConversationFact(BaseModel):
    """One capability's caveated, no-raw-payload fact for the Conversation package.

    Deliberately excludes any field named/shaped like `payload` — `headline` is a derived
    summary string built by `_headline_for`, never the underlying dict.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    source_id: str
    data_class: DataClass
    timing_class: TimingClass
    fetched_at: datetime
    currentness_caveat: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    is_available: bool


class ConversationPackage(BaseModel):
    """M5N — the safe artifact assembled from Canonical + Observation, no raw payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    built_at: datetime
    non_trading_notice: str = Field(
        default=(
            "This is analytical context with explicit currentness caveats — not a trading "
            "signal, price target, or position recommendation."
        ),
        min_length=1,
    )
    facts: tuple[ConversationFact, ...] = Field(default_factory=tuple)

    def fact_for(self, capability: Capability) -> ConversationFact | None:
        for fact in self.facts:
            if fact.capability is capability:
                return fact
        return None


def _headline_for(capability: Capability, result: SourceResult) -> str:
    if result.health.status is not HealthStatus.OK or not result.payload:
        return f"{capability.value}: no current data available ({result.health.status.value})."

    for key in _KNOWN_LIST_KEYS:
        rows = result.payload.get(key)
        if isinstance(rows, list):
            row_count = len(cast("list[object]", rows))
            return f"{capability.value}: {row_count} record(s) available."
    for key in _KNOWN_SINGLE_KEYS:
        if key in result.payload:
            return f"{capability.value}: 1 record available."
    return f"{capability.value}: data available."


def _fact_from_result(capability: Capability, result: SourceResult) -> ConversationFact:
    return ConversationFact(
        capability=capability,
        source_id=result.source_id,
        data_class=result.data_class,
        timing_class=result.timing_class,
        fetched_at=result.fetched_at,
        currentness_caveat=result.currentness_caveat,
        headline=_headline_for(capability, result),
        is_available=result.health.status is HealthStatus.OK and bool(result.payload),
    )


def build_conversation_package(
    symbol: Symbol,
    *,
    canonical: CanonicalPackage | None = None,
    observations: tuple[ObservationRun, ...] = (),
) -> ConversationPackage:
    """Derive the M5N safe artifact from a Canonical package and/or Bounded Observations.

    Observation entries are layered on top of (and take precedence over) canonical ones for
    the same capability, since a bounded observation is the more recent, explicitly
    requested check — but both remain fully caveated and neither exposes a raw payload.
    """
    facts_by_capability: dict[Capability, ConversationFact] = {}
    if canonical is not None:
        for record in canonical.records:
            facts_by_capability[record.capability] = _fact_from_result(
                record.capability, record.result
            )
    for run in observations:
        facts_by_capability[run.capability] = _fact_from_result(run.capability, run.result)

    return ConversationPackage(
        symbol=symbol,
        built_at=datetime.now(UTC),
        facts=tuple(facts_by_capability.values()),
    )


__all__ = ["ConversationFact", "ConversationPackage", "build_conversation_package"]
