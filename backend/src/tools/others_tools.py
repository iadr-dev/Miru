"""Remaining FinMind Others-category tools not already covered elsewhere —
`active_etf_info` (the free-tier ETF roster; the sponsor-tier holdings/holding-change side is
`get_active_etf_report`/`get_etf_money_flow` in `tools/market/`) and `get_finmind_stock_news`,
FinMind's own per-symbol news dataset — distinct from `sources/news.py`'s RSS/Finnhub aggregator,
which `tools/news_tools.py` wraps separately (`get_news_feed`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import ActiveEtfInfoRow, StockNewsRow
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput, MarketOnlyInput


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetActiveEtfRosterOutput(_RowsOutput):
    rows: tuple[ActiveEtfInfoRow, ...] = Field(default_factory=tuple)


class GetActiveEtfRosterTool(BaseTool):
    """`get_active_etf_roster` — wraps `Capability.ACTIVE_ETF_INFO` (the free-tier active-ETF
    roster/basic-info list)."""

    name = "get_active_etf_roster"
    description = (
        "Get the whole-market active-ETF roster/basic-info list (which ETFs are active-management)."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetActiveEtfRosterOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.ACTIVE_ETF_INFO,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ActiveEtfInfoRow,
            key="info",
        )
        return GetActiveEtfRosterOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetFinmindStockNewsOutput(_RowsOutput):
    rows: tuple[StockNewsRow, ...] = Field(default_factory=tuple)


class GetFinmindStockNewsTool(BaseTool):
    """`get_finmind_stock_news` — wraps `Capability.STOCK_NEWS` (FinMind's own news dataset;
    single trading day per request, per-symbol). Named distinctly from `tools/news_tools.py`'s
    `get_news_feed` (the RSS/Finnhub aggregator over `sources/news.py`'s `NEWS` capability) —
    the two are separate real datasets, never merged into one row shape."""

    name = "get_finmind_stock_news"
    description = (
        "Get FinMind's own news dataset rows for one stock, one trading day at a time. "
        "Third-party content — cite, never restate as analysis."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetFinmindStockNewsOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.STOCK_NEWS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=StockNewsRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetFinmindStockNewsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetActiveEtfRosterTool(router), False),
        (GetFinmindStockNewsTool(router), False),
    ]


__all__ = [
    "GetActiveEtfRosterOutput",
    "GetActiveEtfRosterTool",
    "GetFinmindStockNewsOutput",
    "GetFinmindStockNewsTool",
    "build_registration_table",
]
