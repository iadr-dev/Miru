"""Health/diagnostics tools — `get_source_health`, `get_source_matrix`.

Neither tool touches `sources/` directly: `get_source_health` builds its own canonical fetch
via `context/builder.py` and reduces it through `context/source_health.py`; `get_source_matrix`
reads the router's own `source_matrix()` (an adapter-shape-free `{source_id -> {(capability,
market)}}` dict, no `SourceAdapter` import needed here).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from context.builder import build_canonical_context
from context.source_health import SourceHealthEntry, source_health_from_canonical
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class SourceHealthEntryOut(BaseModel):
    """One capability's currentness/closure diagnostic — mirrors `SourceHealthEntry` without
    re-exporting `context/`'s own model as a tool output type."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    source_id: str
    status: HealthStatus
    detail: str = Field(min_length=1)
    currentness_caveat: str = Field(min_length=1)


class GetSourceHealthOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    all_ok: bool
    entries: tuple[SourceHealthEntryOut, ...]


def _entry_out(entry: SourceHealthEntry) -> SourceHealthEntryOut:
    return SourceHealthEntryOut(
        capability=entry.capability,
        source_id=entry.source_id,
        status=entry.health.status,
        detail=entry.health.detail,
        currentness_caveat=entry.currentness_caveat,
    )


class GetSourceHealthTool(BaseTool):
    """`get_source_health` — wraps `context/builder.py` + `context/source_health.py` for one
    `(symbol, market)`. Immediate core."""

    name = "get_source_health"
    description = (
        "Get the source-health diagnostics (which upstreams are OK/stale/unreachable, and "
        "since when) behind a symbol's canonical context build."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetSourceHealthOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        canonical, _health = await build_canonical_context(
            self._router, symbol, tls_policy=ctx.tls_policy
        )
        report = source_health_from_canonical(canonical)
        return GetSourceHealthOutput(
            symbol=report.symbol.id,
            market=report.symbol.market.value,
            all_ok=report.all_ok,
            entries=tuple(_entry_out(entry) for entry in report.entries),
        )


class GetSourceMatrixInput(BaseModel):
    """No symbol needed — the matrix is adapter-level, not per-symbol. Still not a
    full-market-scan surface: it never returns per-symbol data, only which `(capability,
    market)` pairs each configured adapter declares."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceMatrixEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    capability: Capability
    market: Market


class GetSourceMatrixOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[SourceMatrixEntry, ...]


class GetSourceMatrixTool(BaseTool):
    """`get_source_matrix` — wraps `DataAccessRouter.source_matrix()`. Public, deferred."""

    name = "get_source_matrix"
    description = (
        "List every configured source adapter and the (capability, market) pairs it declares."
    )
    input_model: type[BaseModel] = GetSourceMatrixInput
    output_model: type[BaseModel] = GetSourceMatrixOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        parse_args(GetSourceMatrixInput, args)
        matrix = self._router.source_matrix()
        entries = tuple(
            SourceMatrixEntry(source_id=source_id, capability=cap, market=market)
            for source_id, pairs in matrix.items()
            for cap, market in sorted(pairs, key=lambda pair: (pair[0].value, pair[1].value))
        )
        return GetSourceMatrixOutput(entries=entries)


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table (the immediate-core:
    `get_source_health`; `get_source_matrix` is real but not in the named core, so deferred)."""
    tools: list[tuple[Tool, bool]] = [
        (GetSourceHealthTool(router), True),
        (GetSourceMatrixTool(router), False),
    ]
    return tools


__all__ = [
    "GetSourceHealthOutput",
    "GetSourceHealthTool",
    "GetSourceMatrixInput",
    "GetSourceMatrixOutput",
    "GetSourceMatrixTool",
    "SourceHealthEntryOut",
    "SourceMatrixEntry",
    "build_registration_table",
]
