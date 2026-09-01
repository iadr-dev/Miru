"""Realtime tools — the FinMind Realtime-category bounded intraday snapshot datasets
(`sources/finmind/realtime.py`). Every tool here takes `SymbolInput` (a required single
`id`) — FinMind's own docs note these datasets also accept an empty `data_id` meaning "every
symbol", but that whole-market form is never exposed through a request-path tool (the
full-market-scan guard's spirit: whole-market reads belong to the ingest layer only, never a
per-call fan-out this layer could be asked to repeat on every turn). `TICK_SNAPSHOT`/
`FUTURES_SNAPSHOT`/`OPTIONS_SNAPSHOT` are all `TimingClass.LIVE_ISH` — a single bounded
observation, never a live stream or poll, the same framing `live_tools.GetLiveQuoteSnapshotTool`
uses for its own free TWSE MIS snapshot.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import FuturesSnapshotRow, OptionsSnapshotRow, TickSnapshotRow
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetTickSnapshotOutput(_RowsOutput):
    rows: tuple[TickSnapshotRow, ...] = Field(default_factory=tuple)


class GetTickSnapshotTool(BaseTool):
    """`get_tick_snapshot` — wraps `Capability.TICK_SNAPSHOT` (`taiwan_stock_tick_snapshot`,
    Sponsor-tier). `id` accepts either a 4-digit stock/ETF id or a 3-digit broad-market/sector/
    themed/leverage index code — see `TickSnapshotRow`'s own docstring."""

    name = "get_tick_snapshot"
    description = (
        "Get one bounded intraday tick-level snapshot (price/volume/best bid-ask) for one "
        "stock/ETF or index code (Sponsor-tier) — a single explicit check, never a stream."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTickSnapshotOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TICK_SNAPSHOT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TickSnapshotRow,
            key="rows",
        )
        return GetTickSnapshotOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetFuturesSnapshotOutput(_RowsOutput):
    rows: tuple[FuturesSnapshotRow, ...] = Field(default_factory=tuple)


class GetFuturesSnapshotTool(BaseTool):
    """`get_futures_snapshot` — wraps `Capability.FUTURES_SNAPSHOT` (`taiwan_futures_snapshot`,
    Sponsor-tier)."""

    name = "get_futures_snapshot"
    description = (
        "Get one bounded intraday futures-contract snapshot (price/volume/best bid-ask) for "
        "one contract code (Sponsor-tier) — a single explicit check, never a stream."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetFuturesSnapshotOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.FUTURES_SNAPSHOT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=FuturesSnapshotRow,
            key="rows",
        )
        return GetFuturesSnapshotOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetOptionsSnapshotOutput(_RowsOutput):
    rows: tuple[OptionsSnapshotRow, ...] = Field(default_factory=tuple)


class GetOptionsSnapshotTool(BaseTool):
    """`get_options_snapshot` — wraps `Capability.OPTIONS_SNAPSHOT` (`taiwan_options_snapshot`,
    Sponsor-tier)."""

    name = "get_options_snapshot"
    description = (
        "Get one bounded intraday options-contract snapshot (price/volume/best bid-ask) for "
        "one contract code (Sponsor-tier) — a single explicit check, never a stream."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetOptionsSnapshotOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.OPTIONS_SNAPSHOT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=OptionsSnapshotRow,
            key="rows",
        )
        return GetOptionsSnapshotOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetTickSnapshotTool(router), False),
        (GetFuturesSnapshotTool(router), False),
        (GetOptionsSnapshotTool(router), False),
    ]


__all__ = [
    "GetFuturesSnapshotOutput",
    "GetFuturesSnapshotTool",
    "GetOptionsSnapshotOutput",
    "GetOptionsSnapshotTool",
    "GetTickSnapshotOutput",
    "GetTickSnapshotTool",
    "build_registration_table",
]
