"""Shared stub adapter + router builder for context-pipeline tests — no real `sources/*`
(the router itself performs no I/O; these tests exercise `context/` against a stub router,
matching the pattern already established in `tests/dataaccess/test_router.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult


class StubAdapter:
    """A configurable stub `SourceAdapter` — returns a canned `SourceResult` per capability."""

    def __init__(
        self,
        source_id: str,
        *,
        supported: set[tuple[Capability, Market]],
        results: dict[Capability, SourceResult] | None = None,
    ) -> None:
        self.source_id = source_id
        self.timing_class = TimingClass.OFFICIAL_EOD
        self._supported = supported
        self._results = results or {}

    def supports(self) -> set[tuple[Capability, Market]]:
        return self._supported

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        return self._results.get(cap, _default_result(self.source_id, cap))

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


def _default_result(source_id: str, cap: Capability) -> SourceResult:
    return SourceResult(
        source_id=source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=f"stub caveat for {cap.value}",
        payload={"bars": [{"close": 100.0}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def make_router(
    symbol: Symbol,
    results_by_capability: dict[Capability, SourceResult],
) -> DataAccessRouter:
    """Build a `DataAccessRouter` wired to one stub adapter that answers every capability
    present in `results_by_capability` for `symbol.market`, and an `unavailable`-by-omission
    default for anything else (matching the real router's graceful-degradation behavior)."""
    supported = {(cap, symbol.market) for cap in results_by_capability}
    adapter = StubAdapter("STUB", supported=supported, results=results_by_capability)
    priority = {symbol.market.value: {cap.value: ["STUB"] for cap in results_by_capability}}
    return DataAccessRouter({"STUB": adapter}, priority_config=priority)


__all__ = ["StubAdapter", "make_router"]
