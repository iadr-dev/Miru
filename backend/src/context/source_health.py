"""Source Health (M5Q) — `context/source_health.py`.

Aggregates the `SourceHealth`/`HealthStatus` diagnostics carried on every `SourceResult` that
fed a Canonical/Observation/Conversation build into one **diagnostics view**: which sources
were OK/stale/unreachable/unavailable-tier, when, and for which capability. This is what
`GET /health` and MCP's `get_source_health` (future tool layer) will surface.

No I/O — purely a reducer over already-fetched `SourceResult`s (or a `CanonicalPackage`'s
records / an `ObservationRun`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from context.canonical import CanonicalPackage
from context.observation import ObservationRun
from schemas.enums import Capability
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult


class SourceHealthEntry(BaseModel):
    """One capability's currentness/closure diagnostic at build time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    source_id: str
    health: SourceHealth
    fetched_at: datetime
    currentness_caveat: str


class SourceHealthReport(BaseModel):
    """M5Q — the aggregated diagnostics view for one `(symbol, market)` build."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    entries: tuple[SourceHealthEntry, ...]

    @property
    def all_ok(self) -> bool:
        return all(entry.health.status is HealthStatus.OK for entry in self.entries)

    def status_for(self, capability: Capability) -> HealthStatus | None:
        for entry in self.entries:
            if entry.capability is capability:
                return entry.health.status
        return None


def _entry_from_result(capability: Capability, result: SourceResult) -> SourceHealthEntry:
    return SourceHealthEntry(
        capability=capability,
        source_id=result.source_id,
        health=result.health,
        fetched_at=result.fetched_at,
        currentness_caveat=result.currentness_caveat,
    )


def build_source_health_report(
    symbol: Symbol,
    results: dict[Capability, SourceResult],
) -> SourceHealthReport:
    """Aggregate a set of `(capability -> SourceResult)` into one M5Q diagnostics report.

    This is the primitive `context/builder.py` calls right after fetching each capability
    from the router — including capabilities the router had to surface as `unavailable`
    (e.g. no configured/available adapter, such as TAIFEX derivatives today), so Source
    Health degrades gracefully rather than the builder crashing.
    """
    entries = tuple(_entry_from_result(cap, result) for cap, result in results.items())
    return SourceHealthReport(symbol=symbol, entries=entries)


def source_health_from_canonical(package: CanonicalPackage) -> SourceHealthReport:
    """Derive an M5Q report from an already-built `CanonicalPackage`'s preserved records."""
    entries = tuple(
        _entry_from_result(record.capability, record.result) for record in package.records
    )
    return SourceHealthReport(symbol=package.symbol, entries=entries)


def source_health_from_observation(run: ObservationRun) -> SourceHealthReport:
    """Derive a single-entry M5Q report from one Bounded Observation run."""
    return SourceHealthReport(
        symbol=run.symbol,
        entries=(_entry_from_result(run.capability, run.result),),
    )


__all__ = [
    "SourceHealthEntry",
    "SourceHealthReport",
    "build_source_health_report",
    "source_health_from_canonical",
    "source_health_from_observation",
]
