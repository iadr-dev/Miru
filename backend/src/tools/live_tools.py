"""TWSE MIS bounded live-ish snapshot tool — wraps `sources/twse_mis.py` via the router
(`Capability.PRICE_INTRADAY`, TW-only).

Distinct from `tools/context_tools.py`'s `run_bounded_observation`: that tool is capability-
agnostic (it can bound-check *any* capability the router serves, one at a time, and returns the
generic `CapabilitySummary` redaction). This tool is a named, purpose-built wrapper specifically
for the one free live-ish source (`TWSE_MIS`) with a typed snapshot output — the design's
`run_bounded_observation` (public, generic) vs a dedicated snapshot tool are complementary, not
duplicative: an agent that already knows it wants "the TWSE MIS quote" calls this one directly
instead of round-tripping through the generic capability-summary shape.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class LiveQuoteSnapshot(BaseModel):
    """One TWSE MIS best-5 bid/ask + last-trade snapshot row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    name: str | None = None
    last_price: float
    open: float
    high: float
    low: float
    volume: int
    best_bid: str | None = None
    best_ask: str | None = None
    observed_at: str | None = None


class GetLiveQuoteSnapshotOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    snapshot: LiveQuoteSnapshot | None = None


class GetLiveQuoteSnapshotTool(BaseTool):
    """`get_live_quote_snapshot` — one explicit, bounded TWSE MIS observation (never a poll)
    for one TW stock/ETF symbol."""

    name = "get_live_quote_snapshot"
    description = (
        "Get one bounded, delayed live-ish quote snapshot (best-5 bid/ask + last trade) for "
        "one TW stock/ETF symbol — a single explicit check, never a stream or poll."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetLiveQuoteSnapshotOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "live_quote_snapshot"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.PRICE_INTRADAY, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_snapshot = result.payload.get("snapshot")
        snapshot = (
            LiveQuoteSnapshot.model_validate(cast("dict[str, object]", raw_snapshot))
            if isinstance(raw_snapshot, dict)
            else None
        )
        return GetLiveQuoteSnapshotOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            snapshot=snapshot,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetLiveQuoteSnapshotTool(router), False)]


__all__ = [
    "GetLiveQuoteSnapshotOutput",
    "GetLiveQuoteSnapshotTool",
    "LiveQuoteSnapshot",
    "build_registration_table",
]
