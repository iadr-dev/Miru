"""Chip/institutional-flow tools — the FinMind Chip/Institutional-category datasets not already
wired in `market_tools.py` (`institutional_flow`, `chip_broker`). Every tool here follows the
same "fetch one capability, validate its rows" shape (`tools/_rows.py`); `data_class` mirrors
each dataset's own `DataClass` in `sources/finmind/chip_institutional.py`/`others.py` exactly —
several of these are Sponsor-tier and must stay gated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import PRICE_EOD_JOB
from ingest.store import ParquetStore
from schemas.capabilities import (
    BlockTradeBrokerReportRow,
    BlockTradeRow,
    ChipBrokerAggRow,
    ChipBrokerRow,
    DailyShortSaleBalancesRow,
    DispositionSecuritiesRow,
    ForeignShareholdingRow,
    GovernmentBankBuySellRow,
    IndustryChainClassificationRow,
    IndustryChainMoneyFlowRow,
    InstitutionalFlowTotalMarketRow,
    InstitutionalFlowWideRow,
    LoanCollateralBalanceRow,
    MarginMaintenanceRatioRow,
    MarginShortRow,
    MarginShortSaleSuspensionRow,
    MarginShortTotalMarketRow,
    SecuritiesLendingRow,
    SecuritiesTraderInfoRow,
    ShareholdingDistributionRow,
)
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, is_entitled, parse_args
from tools.common import DateRangeInput, MarketOnlyInput, candidate_trading_days


class _RowsOutput(BaseModel):
    """Shared output shape: source provenance + a tuple of normalized rows. Each concrete
    tool below subclasses this once per row model so MCP still sees a distinctly-named,
    distinctly-typed output schema per tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetMarginShortOutput(_RowsOutput):
    rows: tuple[MarginShortRow, ...] = Field(default_factory=tuple)


class GetMarginShortTool(BaseTool):
    """`get_margin_short` — wraps `Capability.MARGIN_SHORT` (per-stock 融資融劵 balance)."""

    name = "get_margin_short"
    description = "Get daily margin-purchase/short-sale (融資融劵) balance rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMarginShortOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "margin_short"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARGIN_SHORT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarginShortRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarginShortOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetMarginShortTotalMarketOutput(_RowsOutput):
    rows: tuple[MarginShortTotalMarketRow, ...] = Field(default_factory=tuple)


class GetMarginShortTotalMarketTool(BaseTool):
    """`get_margin_short_total_market` — wraps `Capability.MARGIN_SHORT_TOTAL_MARKET`
    (whole-market margin/short-sale total by category; market-level, no per-symbol scope)."""

    name = "get_margin_short_total_market"
    description = "Get the whole-market margin-purchase/short-sale total by category for a market."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetMarginShortTotalMarketOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "margin_short_total_market"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARGIN_SHORT_TOTAL_MARKET,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarginShortTotalMarketRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarginShortTotalMarketOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetInstitutionalFlowWideOutput(_RowsOutput):
    rows: tuple[InstitutionalFlowWideRow, ...] = Field(default_factory=tuple)


class GetInstitutionalFlowWideTool(BaseTool):
    """`get_institutional_flow_wide` — wraps `Capability.INSTITUTIONAL_FLOW_WIDE` (same data
    as `get_institutional_flow`, reshaped one row per day with a column per investor category)."""

    name = "get_institutional_flow_wide"
    description = (
        "Get the three-major-institutional-investor buy/sell for one stock, reshaped one row "
        "per day with a dedicated buy/sell column per investor category."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetInstitutionalFlowWideOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "institutional_flow_wide"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INSTITUTIONAL_FLOW_WIDE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=InstitutionalFlowWideRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetInstitutionalFlowWideOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetInstitutionalFlowTotalMarketOutput(_RowsOutput):
    rows: tuple[InstitutionalFlowTotalMarketRow, ...] = Field(default_factory=tuple)


class GetInstitutionalFlowTotalMarketTool(BaseTool):
    """`get_institutional_flow_total_market` — wraps `Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET`
    (whole-market three-major-institutional-investor buy/sell total by category)."""

    name = "get_institutional_flow_total_market"
    description = (
        "Get the whole-market three-major-institutional-investor buy/sell total by category."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetInstitutionalFlowTotalMarketOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "institutional_flow_total_market"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=InstitutionalFlowTotalMarketRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetInstitutionalFlowTotalMarketOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetForeignShareholdingOutput(_RowsOutput):
    rows: tuple[ForeignShareholdingRow, ...] = Field(default_factory=tuple)


class GetForeignShareholdingTool(BaseTool):
    """`get_foreign_shareholding` — wraps `Capability.FOREIGN_SHAREHOLDING` (外資持股)."""

    name = "get_foreign_shareholding"
    description = "Get daily foreign-shareholding-limit/ratio rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetForeignShareholdingOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "foreign_shareholding"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.FOREIGN_SHAREHOLDING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ForeignShareholdingRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetForeignShareholdingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetShareholderDispersionOutput(_RowsOutput):
    rows: tuple[ShareholdingDistributionRow, ...] = Field(default_factory=tuple)


class GetShareholderDispersionTool(BaseTool):
    """`get_shareholder_dispersion` — 股東人數/千張大戶比率 concentration signal, wrapping
    `Capability.SHAREHOLDING_DISTRIBUTION` (`TaiwanStockHoldingSharesPer`, Sponsor-tier)."""

    name = "get_shareholder_dispersion"
    description = (
        "Get the shareholding-level distribution (股權分散) rows for one stock — a "
        "chip-concentration signal (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetShareholderDispersionOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "shareholder_dispersion"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SHAREHOLDING_DISTRIBUTION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ShareholdingDistributionRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetShareholderDispersionOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetSecuritiesLendingOutput(_RowsOutput):
    rows: tuple[SecuritiesLendingRow, ...] = Field(default_factory=tuple)


class GetSecuritiesLendingTool(BaseTool):
    """`get_securities_lending` — wraps `Capability.SECURITIES_LENDING` (借券成交)."""

    name = "get_securities_lending"
    description = "Get daily securities-lending (借券) transaction rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetSecuritiesLendingOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "securities_lending"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SECURITIES_LENDING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SecuritiesLendingRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetSecuritiesLendingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetMarginShortSaleSuspensionOutput(_RowsOutput):
    rows: tuple[MarginShortSaleSuspensionRow, ...] = Field(default_factory=tuple)


class GetMarginShortSaleSuspensionTool(BaseTool):
    """`get_margin_short_sale_suspension` — wraps `Capability.MARGIN_SHORT_SALE_SUSPENSION`."""

    name = "get_margin_short_sale_suspension"
    description = "Get 暫停融券賣出 (short-sale suspension) announcement rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMarginShortSaleSuspensionOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARGIN_SHORT_SALE_SUSPENSION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarginShortSaleSuspensionRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarginShortSaleSuspensionOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetDailyShortSaleBalancesOutput(_RowsOutput):
    rows: tuple[DailyShortSaleBalancesRow, ...] = Field(default_factory=tuple)


class GetDailyShortSaleBalancesTool(BaseTool):
    """`get_daily_short_sale_balances` — wraps `Capability.DAILY_SHORT_SALE_BALANCES`."""

    name = "get_daily_short_sale_balances"
    description = (
        "Get the daily credit-limit short-sale quota/balance rows (margin + "
        "securities-lending) for one stock."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDailyShortSaleBalancesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "daily_short_sale_balances"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.DAILY_SHORT_SALE_BALANCES,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DailyShortSaleBalancesRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetDailyShortSaleBalancesOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetSecuritiesTraderInfoOutput(_RowsOutput):
    rows: tuple[SecuritiesTraderInfoRow, ...] = Field(default_factory=tuple)


class GetSecuritiesTraderInfoTool(BaseTool):
    """`get_securities_trader_info` — wraps `Capability.SECURITIES_TRADER_INFO` (broker
    directory reference table; market-level, no per-symbol scope)."""

    name = "get_securities_trader_info"
    description = "Get the whole-market broker/securities-trader directory reference table."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetSecuritiesTraderInfoOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SECURITIES_TRADER_INFO,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SecuritiesTraderInfoRow,
            key="rows",
        )
        return GetSecuritiesTraderInfoOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetGovernmentBankFlowsOutput(_RowsOutput):
    rows: tuple[GovernmentBankBuySellRow, ...] = Field(default_factory=tuple)


_GOVERNMENT_BANK_MAX_TRADING_DAYS = 10  # Same upstream shape as `chip_broker` (see
# `market_tools.py::_CHIP_BROKER_MAX_TRADING_DAYS`'s own docstring): `TaiwanStockGovernmentBank
# BuySell` 400s if `end_date` is present at all (confirmed empirically — the dataset's own
# `no_end_date_param=True` in chip_institutional.py), so a multi-day request fans out to one
# call per trading day here, capped the same way 分點's own fan-out is.


class GetGovernmentBankFlowsTool(BaseTool):
    """`get_government_bank_flows` — wraps `Capability.GOVERNMENT_BANK_BUYSELL` (八大行庫)."""

    name = "get_government_bank_flows"
    description = (
        "Get eight-government-bank(八大行庫) buy/sell rows for one stock over an optional date "
        "range (Sponsor-tier). The upstream report is single-day-per-call, so a `start_date`/"
        f"`end_date` range fans out to one call per trading day, capped at "
        f"{_GOVERNMENT_BANK_MAX_TRADING_DAYS} days (most recent first) — a wider range silently "
        "returns only the newest days within that cap."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetGovernmentBankFlowsOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "government_bank_flows"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        sym = typed.to_symbol()
        end = typed.end_date or date_.today()
        start = typed.start_date or end

        if start == end:
            days: tuple[date_, ...] = (start,)
        else:
            days = candidate_trading_days(start, end, limit=_GOVERNMENT_BANK_MAX_TRADING_DAYS) or (
                end,
            )

        results = await asyncio.gather(
            *(
                fetch_rows(
                    self._router,
                    Capability.GOVERNMENT_BANK_BUYSELL,
                    sym,
                    tls_policy=ctx.tls_policy,
                    model=GovernmentBankBuySellRow,
                    key="rows",
                    # `start_date` (with no `end_date`) is the real single-day param this
                    # dataset's upstream expects — confirmed empirically against the live
                    # endpoint (see `_DATASETS[Capability.GOVERNMENT_BANK_BUYSELL]`'s own
                    # comment in chip_institutional.py); `date` 400s outright.
                    params={"start_date": day.isoformat()},
                )
                for day in days
            )
        )

        rows: list[GovernmentBankBuySellRow] = []
        for _result, day_rows in results:
            rows.extend(day_rows)

        # Every per-day fetch shares the same `source_id`/`currentness_caveat` (one adapter, one
        # dataset) — the last result's health represents the most recent day's own fetch outcome,
        # same convention `GetChipFlowsTool` (market_tools.py) already uses for its own fan-out.
        representative, _ = results[-1]
        return GetGovernmentBankFlowsOutput(
            source_id=representative.source_id,
            currentness_caveat=representative.currentness_caveat,
            health_status=representative.health.status,
            rows=tuple(rows),
        )


class GetWarrantChipBrokerOutput(_RowsOutput):
    rows: tuple[ChipBrokerRow, ...] = Field(default_factory=tuple)


class GetWarrantChipBrokerTool(BaseTool):
    """`get_warrant_chip_broker` — wraps `Capability.WARRANT_CHIP_BROKER`
    (`TaiwanStockWarrantTradingDailyReport`, 權證分點進出, Sponsor-tier). The warrant-side
    counterpart to `market_tools.GetChipFlowsTool`'s equity 分點 report — same row shape
    (`ChipBrokerRow`), different underlying dataset, kept as its own tool rather than folded
    into `get_chip_flows` since a warrant code and an equity code are never interchangeable
    inputs to the same call. Single trading day per request upstream — `no_end_date_param=True`
    in the source config (confirmed empirically: 400s with `end_date` present at all)."""

    name = "get_warrant_chip_broker"
    description = (
        "Get branch(分點)/broker-level buy-sell trading rows for one warrant, one trading day "
        "at a time (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetWarrantChipBrokerOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.WARRANT_CHIP_BROKER,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ChipBrokerRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetWarrantChipBrokerOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetMarginMaintenanceOutput(_RowsOutput):
    rows: tuple[MarginMaintenanceRatioRow, ...] = Field(default_factory=tuple)
    # `chart_hint` (schemas/chart_hint.py): a bounded warning-threshold ratio hovering near a
    # danger line reads better filled (`area`) than as a bare `line` — the first real caller of
    # that mark.
    chart_hint: ChartHint | None = None


class GetMarginMaintenanceTool(BaseTool):
    """`get_margin_maintenance` — wraps `Capability.MARGIN_MAINTENANCE_RATIO` (大盤融資維持率;
    market-level, no per-symbol scope)."""

    name = "get_margin_maintenance"
    description = (
        "Get the whole-market margin-maintenance-ratio (融資維持率) reading (Sponsor-tier)."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetMarginMaintenanceOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "margin_maintenance"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARGIN_MAINTENANCE_RATIO,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarginMaintenanceRatioRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarginMaintenanceOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            chart_hint=ChartHint(
                mark=Mark.AREA,
                x=FieldDef(field="trade_date", type=FieldType.TEMPORAL),
                y=FieldDef(field="total_exchange_margin_maintenance", type=FieldType.QUANTITATIVE),
            )
            if rows
            else None,
        )


class GetChipBrokerAggOutput(_RowsOutput):
    rows: tuple[ChipBrokerAggRow, ...] = Field(default_factory=tuple)


class GetChipBrokerAggTool(BaseTool):
    """`get_chip_broker_agg` — wraps `Capability.CHIP_BROKER_AGG` (per-broker aggregated
    分點 buy/sell volume/price; distinct from `get_chip_flows`'s per-branch rows)."""

    name = "get_chip_broker_agg"
    description = (
        "Get per-broker aggregated branch(分點) buy/sell volume/price for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetChipBrokerAggOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "chip_broker_agg"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.CHIP_BROKER_AGG,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ChipBrokerAggRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetChipBrokerAggOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetBlockTradeBrokerReportOutput(_RowsOutput):
    rows: tuple[BlockTradeBrokerReportRow, ...] = Field(default_factory=tuple)


class GetBlockTradeBrokerReportTool(BaseTool):
    """`get_block_trade_broker_report` — wraps `Capability.BLOCK_TRADE_BROKER_REPORT`."""

    name = "get_block_trade_broker_report"
    description = (
        "Get broker-level 鉅額交易 (block-trade) buy/sell rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetBlockTradeBrokerReportOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "block_trade_broker_report"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.BLOCK_TRADE_BROKER_REPORT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=BlockTradeBrokerReportRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetBlockTradeBrokerReportOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetBlockTradeOutput(_RowsOutput):
    rows: tuple[BlockTradeRow, ...] = Field(default_factory=tuple)


class GetBlockTradeTool(BaseTool):
    """`get_block_trade` — wraps `Capability.BLOCK_TRADE` (鉅額交易 execution report)."""

    name = "get_block_trade"
    description = "Get 鉅額交易 (block-trade) execution rows for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetBlockTradeOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "block_trade"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.BLOCK_TRADE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=BlockTradeRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetBlockTradeOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetLoanCollateralBalanceOutput(_RowsOutput):
    rows: tuple[LoanCollateralBalanceRow, ...] = Field(default_factory=tuple)


class GetLoanCollateralBalanceTool(BaseTool):
    """`get_loan_collateral_balance` — wraps `Capability.LOAN_COLLATERAL_BALANCE`."""

    name = "get_loan_collateral_balance"
    description = "Get the loan-collateral balance rows for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetLoanCollateralBalanceOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "loan_collateral_balance"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.LOAN_COLLATERAL_BALANCE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=LoanCollateralBalanceRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetLoanCollateralBalanceOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


_INDUSTRY_MONEY_FLOW_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception


def _ranked_industry_money_flow_rows(
    rows: Sequence[IndustryChainMoneyFlowRow],
) -> tuple[IndustryChainMoneyFlowRow, ...]:
    """Stamps each row with a 1-based `rank`, ordered by `trading_money_pct` descending — the
    same order the frontend card itself sorts by, so the free-preview rows are the most
    prominent ones a caller would see first. Chain-total rows (`sub_industry == ""`) and
    sub-industry breakdown rows are ranked as two SEPARATE lists (mirroring
    `_ranked_cards_by_bucket`'s per-bucket approach) — they're two distinct rankings, not one
    combined list, so a sub-industry row's rank must not depend on how many chain totals exist."""
    totals = [r for r in rows if r.is_total]
    breakdown = [r for r in rows if not r.is_total]
    ranked: list[IndustryChainMoneyFlowRow] = []
    for group in (totals, breakdown):
        ordered = sorted(group, key=lambda r: r.trading_money_pct or 0.0, reverse=True)
        ranked.extend(row.model_copy(update={"rank": i}) for i, row in enumerate(ordered, start=1))
    return tuple(ranked)


def _mask_industry_money_flow_rows_beyond(
    rows: Sequence[IndustryChainMoneyFlowRow], free_row_count: int
) -> tuple[IndustryChainMoneyFlowRow, ...]:
    """Rows beyond `free_row_count` get every identity/value field nulled server-side — `rank`/
    `market`/`trade_date` survive, matching `_mask_broker_top_rows_beyond`'s contract."""
    masked: list[IndustryChainMoneyFlowRow] = []
    for row in rows:
        if row.rank <= free_row_count:
            masked.append(row)
            continue
        masked.append(
            IndustryChainMoneyFlowRow(
                rank=row.rank,
                market=row.market,
                trade_date=row.trade_date,
                is_total=row.is_total,
                industry=None,
                sub_industry=None,
                stock_count=None,
                trading_volume=None,
                trading_money=None,
                trading_money_pct=None,
                locked=True,
            )
        )
    return tuple(masked)


class GetIndustryMoneyFlowOutput(_RowsOutput):
    free_row_count: int = 0  # governance.md §4 top-N-preview exception
    rows: tuple[IndustryChainMoneyFlowRow, ...] = Field(default_factory=tuple)


class GetIndustryMoneyFlowTool(BaseTool):
    """`get_industry_money_flow` — wraps `Capability.INDUSTRY_CHAIN_MONEY_FLOW` (產業鏈資金流;
    market-level, single trading day per request).

    `TaiwanStockIndustryChainMoneyFlow` upstream treats `start_date` as the literal ONE day to
    fetch, not a range start (confirmed empirically: `start_date=<30 days ago>` — the FinMind
    adapter's own generic default-lookback fill-in for an omitted date — silently returns zero
    rows every time, while `start_date=<the real latest trading day>` returns real rows). A
    caller that omits `start_date` must therefore get an explicit, real trading day resolved
    HERE — `PRICE_EOD_JOB`'s own latest already-ingested partition date, never the adapter's
    30-day-back default, which this dataset's own single-day semantics turns into a guaranteed
    empty result."""

    name = "get_industry_money_flow"
    description = (
        "Get the whole-market industry-chain money-flow (產業鏈資金流) aggregate (Sponsor-tier)."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetIndustryMoneyFlowOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "industry_money_flow"
    supports_row_preview: bool = True

    def __init__(self, router: DataAccessRouter, store: ParquetStore) -> None:
        self._router = router
        self._store = store

    def _resolve_trade_date(self, market: str, requested: date_ | None) -> date_ | None:
        if requested is not None:
            return requested
        known_dates = self._store.list_partition_dates(PRICE_EOD_JOB.dataset, market)
        if not known_dates:
            return None
        # `PRICE_EOD_JOB`'s own latest ingested partition date is itself always a real trading
        # day (the ingest job never writes a partition for a non-trading day), so no further
        # calendar/previous-trading-day shift is needed on top of it.
        return max(known_dates)

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        trade_date = self._resolve_trade_date(typed.market.value, typed.start_date)
        params = dict(typed.date_params())
        if trade_date is not None:
            params["start_date"] = trade_date.isoformat()
            params.pop("end_date", None)  # this dataset 400s if end_date is present at all
        result, rows = await fetch_rows(
            self._router,
            Capability.INDUSTRY_CHAIN_MONEY_FLOW,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=IndustryChainMoneyFlowRow,
            key="rows",
            params=params,
        )
        # `fetch_rows` validates straight from the upstream payload, which has no `is_total`
        # field of its own — derive it here (once, before ranking/masking) from `sub_industry`.
        classified_rows = tuple(
            row.model_copy(update={"is_total": row.sub_industry == ""}) for row in rows
        )
        ranked = _ranked_industry_money_flow_rows(classified_rows)
        output_rows = (
            ranked
            if is_entitled(self.data_class, ctx.tier)
            else _mask_industry_money_flow_rows_beyond(ranked, _INDUSTRY_MONEY_FLOW_FREE_ROW_COUNT)
        )
        return GetIndustryMoneyFlowOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            free_row_count=_INDUSTRY_MONEY_FLOW_FREE_ROW_COUNT,
            rows=output_rows,
        )


class GetDispositionSecuritiesOutput(_RowsOutput):
    rows: tuple[DispositionSecuritiesRow, ...] = Field(default_factory=tuple)


class GetDispositionSecuritiesTool(BaseTool):
    """`get_disposition_securities` — wraps `Capability.DISPOSITION_SECURITIES` (公布處置有價證券).

    Narrower than the design's full `get_disposition_stocks` composite (which additionally
    folds in TPEX 注意股/停資停券 and a derived escalation projection) — this tool covers the
    FinMind disposition-event stream only; the attention-stock and forecast composites are not
    built this pass (no adapter exists for the TPEX 注意股 side yet)."""

    name = "get_disposition_securities"
    description = (
        "Get 處置有價證券 (disposition-securities) announcement rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDispositionSecuritiesOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.DISPOSITION_SECURITIES,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DispositionSecuritiesRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetDispositionSecuritiesOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetIndustryChainClassificationOutput(_RowsOutput):
    rows: tuple[IndustryChainClassificationRow, ...] = Field(default_factory=tuple)


class GetIndustryChainClassificationTool(BaseTool):
    """`get_industry_chain_classification` — wraps `Capability.INDUSTRY_CHAIN_CLASSIFICATION`
    (the static roster underlying `get_industry_money_flow`'s daily aggregate)."""

    name = "get_industry_chain_classification"
    description = (
        "Get the static industry-chain (產業鏈) classification rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetIndustryChainClassificationOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INDUSTRY_CHAIN_CLASSIFICATION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=IndustryChainClassificationRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetIndustryChainClassificationOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table — every tool here is a real,
    deferred capability (none are part of the named immediate core). `get_industry_money_flow`
    additionally needs a `ParquetStore` — see its own class docstring for why (resolving the
    real latest trading day from `PRICE_EOD_JOB`'s own ingested calendar, since the FinMind
    adapter's generic 30-day-lookback default silently breaks this dataset's single-day
    semantics) — built here from `config.settings` rather than threaded through this function's
    own signature, same self-contained-store pattern `market_tools.build_registration_table`
    already uses for its own store-reading tools."""
    store = ParquetStore(root=get_settings().parquet_root)
    return [
        (GetMarginShortTool(router), False),
        (GetMarginShortTotalMarketTool(router), False),
        (GetInstitutionalFlowWideTool(router), False),
        (GetInstitutionalFlowTotalMarketTool(router), False),
        (GetForeignShareholdingTool(router), False),
        (GetShareholderDispersionTool(router), False),
        (GetSecuritiesLendingTool(router), False),
        (GetMarginShortSaleSuspensionTool(router), False),
        (GetDailyShortSaleBalancesTool(router), False),
        (GetSecuritiesTraderInfoTool(router), False),
        (GetGovernmentBankFlowsTool(router), False),
        (GetWarrantChipBrokerTool(router), False),
        (GetMarginMaintenanceTool(router), False),
        (GetChipBrokerAggTool(router), False),
        (GetBlockTradeBrokerReportTool(router), False),
        (GetBlockTradeTool(router), False),
        (GetLoanCollateralBalanceTool(router), False),
        (GetIndustryMoneyFlowTool(router, store), False),
        (GetDispositionSecuritiesTool(router), False),
        (GetIndustryChainClassificationTool(router), False),
    ]


__all__ = [
    "GetBlockTradeBrokerReportOutput",
    "GetBlockTradeBrokerReportTool",
    "GetBlockTradeOutput",
    "GetBlockTradeTool",
    "GetChipBrokerAggOutput",
    "GetChipBrokerAggTool",
    "GetDailyShortSaleBalancesOutput",
    "GetDailyShortSaleBalancesTool",
    "GetDispositionSecuritiesOutput",
    "GetDispositionSecuritiesTool",
    "GetForeignShareholdingOutput",
    "GetForeignShareholdingTool",
    "GetGovernmentBankFlowsOutput",
    "GetGovernmentBankFlowsTool",
    "GetIndustryChainClassificationOutput",
    "GetIndustryChainClassificationTool",
    "GetIndustryMoneyFlowOutput",
    "GetIndustryMoneyFlowTool",
    "GetInstitutionalFlowTotalMarketOutput",
    "GetInstitutionalFlowTotalMarketTool",
    "GetInstitutionalFlowWideOutput",
    "GetInstitutionalFlowWideTool",
    "GetLoanCollateralBalanceOutput",
    "GetLoanCollateralBalanceTool",
    "GetMarginMaintenanceOutput",
    "GetMarginMaintenanceTool",
    "GetMarginShortOutput",
    "GetMarginShortSaleSuspensionOutput",
    "GetMarginShortSaleSuspensionTool",
    "GetMarginShortTool",
    "GetMarginShortTotalMarketOutput",
    "GetMarginShortTotalMarketTool",
    "GetSecuritiesLendingOutput",
    "GetSecuritiesLendingTool",
    "GetSecuritiesTraderInfoOutput",
    "GetSecuritiesTraderInfoTool",
    "GetShareholderDispersionOutput",
    "GetShareholderDispersionTool",
    "GetWarrantChipBrokerOutput",
    "GetWarrantChipBrokerTool",
    "build_registration_table",
]
