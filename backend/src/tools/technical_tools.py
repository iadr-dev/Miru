"""Technical-category tools — the FinMind Technical-category datasets not already wired in
`market_tools.py` (`price_eod`). Reference rosters/calendars are market-level
(`MarketOnlyInput`); everything else is per-symbol (`DateRangeInput`). `data_class` mirrors
each dataset's own `DataClass` in `sources/finmind/technical.py` exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    DayTradingRow,
    DayTradingSuspensionRow,
    Index5sSnapshotRow,
    KBarRow,
    OrderBookStatsRow,
    PeriodPriceRow,
    PriceLimitRow,
    PriceTickRow,
    StockInfoRow,
    Taiex5sIndexRow,
    TenYearPriceRow,
    TotalReturnIndexRow,
    TradingCalendarRow,
    TradingSuspensionRow,
    ValuationRatiosRow,
    WarrantUnderlyingRow,
)
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


class GetStockRosterOutput(_RowsOutput):
    rows: tuple[StockInfoRow, ...] = Field(default_factory=tuple)


class GetStockRosterTool(BaseTool):
    """`get_stock_roster` — wraps `Capability.STOCK_INFO_WITH_WARRANT` (the fuller of the two
    reference-roster datasets — `STOCK_INFO` is a strict subset, not separately exposed as its
    own tool)."""

    name = "get_stock_roster"
    description = "Get the whole-market stock/warrant universe reference roster."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetStockRosterOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.STOCK_INFO_WITH_WARRANT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=StockInfoRow,
            key="rows",
        )
        return GetStockRosterOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetWarrantUnderlyingOutput(_RowsOutput):
    rows: tuple[WarrantUnderlyingRow, ...] = Field(default_factory=tuple)


class GetWarrantUnderlyingTool(BaseTool):
    """`get_warrant_underlying` — wraps `Capability.WARRANT_UNDERLYING` (warrant-code-to-
    underlying mapping; resolve by date interval, not the latest row — see the row model)."""

    name = "get_warrant_underlying"
    description = (
        "Get the warrant-code-to-underlying-stock mapping rows for one warrant code (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetWarrantUnderlyingOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.WARRANT_UNDERLYING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=WarrantUnderlyingRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetWarrantUnderlyingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTradingCalendarOutput(_RowsOutput):
    rows: tuple[TradingCalendarRow, ...] = Field(default_factory=tuple)


class GetTradingCalendarTool(BaseTool):
    """`get_trading_calendar` — wraps `Capability.TRADING_CALENDAR` (whole-market trading-day
    reference table)."""

    name = "get_trading_calendar"
    description = "Get the whole-market trading-day calendar reference table."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetTradingCalendarOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TRADING_CALENDAR,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TradingCalendarRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTradingCalendarOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetPriceTickOutput(_RowsOutput):
    rows: tuple[PriceTickRow, ...] = Field(default_factory=tuple)


class GetPriceTickTool(BaseTool):
    """`get_price_tick` — wraps `Capability.PRICE_TICK` (tick-level trade history; single
    trading day per request upstream)."""

    name = "get_price_tick"
    description = (
        "Get tick-level trade history for one stock/ETF, one trading day at a time (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetPriceTickOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "price_tick"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.PRICE_TICK,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=PriceTickRow,
            key="ticks",
            params=typed.date_params(),
        )
        return GetPriceTickOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class ValuationBandSummary(BaseModel):
    """Pre-computed min/max/current PER/PBR/dividend-yield over the whole window a
    `get_valuation_ratios` call returned — a "近一年本益比在哪個位置" question needs a band
    comparison, never a hand-summed scan of hundreds of daily rows. `current` is the most recent
    trading day's own value (not an average), so a caller comparing "current vs range" is
    comparing like with like."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_start: date_
    window_end: date_
    per_min: float
    per_max: float
    per_current: float
    pbr_min: float
    pbr_max: float
    pbr_current: float
    dividend_yield_min: float
    dividend_yield_max: float
    dividend_yield_current: float


def _valuation_band_summary(rows: Sequence[ValuationRatiosRow]) -> ValuationBandSummary | None:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.trade_date)
    latest = ordered[-1]
    return ValuationBandSummary(
        window_start=ordered[0].trade_date,
        window_end=latest.trade_date,
        per_min=min(r.per for r in rows),
        per_max=max(r.per for r in rows),
        per_current=latest.per,
        pbr_min=min(r.pbr for r in rows),
        pbr_max=max(r.pbr for r in rows),
        pbr_current=latest.pbr,
        dividend_yield_min=min(r.dividend_yield for r in rows),
        dividend_yield_max=max(r.dividend_yield for r in rows),
        dividend_yield_current=latest.dividend_yield,
    )


class GetValuationRatiosOutput(_RowsOutput):
    # `band` is declared BEFORE `rows` — `agent.loop._render_result_for_model` serializes this
    # model to JSON in field-declaration order and hard-truncates at a fixed char budget; with
    # `rows` first, a ~year-long daily window's raw rows alone can consume the entire budget and
    # cut `band` out of what the model ever sees (same incident class as
    # `GetInstitutionalFlowOutput.rolling_net`'s own docstring — confirmed live: the model's own
    # reasoning trace showed it struggling to even find the latest date in a truncated raw dump,
    # and gave up without ever stating whether the current PER/PBR was high or low in-range).
    band: ValuationBandSummary | None = None
    rows: tuple[ValuationRatiosRow, ...] = Field(default_factory=tuple)


class GetValuationRatiosTool(BaseTool):
    """`get_valuation_ratios` — wraps `Capability.VALUATION_RATIOS` (daily PER/PBR/dividend
    yield). Also returns `band` — a pre-computed min/max/current summary over the requested
    window; cite `band`'s figures directly for a "is this cheap/expensive relative to its own
    range" question rather than scanning `rows` yourself."""

    name = "get_valuation_ratios"
    description = (
        "Get daily PER/PBR/dividend-yield rows for one stock over an optional date range, plus "
        "a pre-computed min/max/current summary over that window — cite `band`'s figures "
        "directly rather than scanning `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetValuationRatiosOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "valuation_ratios"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.VALUATION_RATIOS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=ValuationRatiosRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetValuationRatiosOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            band=_valuation_band_summary(rows),
            rows=rows,
        )


class GetOrderBookStatsOutput(_RowsOutput):
    rows: tuple[OrderBookStatsRow, ...] = Field(default_factory=tuple)


class GetOrderBookStatsTool(BaseTool):
    """`get_order_book_stats` — wraps `Capability.ORDER_BOOK_STATS` (whole-exchange 5-second
    order-book/trade aggregate; market-level, no per-symbol scope)."""

    name = "get_order_book_stats"
    description = "Get the whole-exchange 5-second order-book/trade aggregate for one trading day."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetOrderBookStatsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "order_book_stats"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.ORDER_BOOK_STATS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=OrderBookStatsRow,
            key="stats",
            params=typed.date_params(),
        )
        return GetOrderBookStatsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTaiexIndexSnapshotOutput(_RowsOutput):
    rows: tuple[Taiex5sIndexRow, ...] = Field(default_factory=tuple)


class GetTaiexIndexSnapshotTool(BaseTool):
    """`get_taiex_index_snapshot` — wraps `Capability.TAIEX_5S_INDEX` (whole-market TAIEX
    5-second index reading)."""

    name = "get_taiex_index_snapshot"
    description = "Get the whole-market TAIEX 5-second index reading for one trading day."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetTaiexIndexSnapshotOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "taiex_index_snapshot"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TAIEX_5S_INDEX,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=Taiex5sIndexRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTaiexIndexSnapshotOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetDayTradingOutput(_RowsOutput):
    rows: tuple[DayTradingRow, ...] = Field(default_factory=tuple)


class GetDayTradingTool(BaseTool):
    """`get_day_trading` — wraps `Capability.DAY_TRADING` (當沖 eligibility + volume/value)."""

    name = "get_day_trading"
    description = "Get day-trading (當沖) eligibility marker + volume/value rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDayTradingOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "day_trading"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.DAY_TRADING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DayTradingRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetDayTradingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTotalReturnIndexOutput(_RowsOutput):
    rows: tuple[TotalReturnIndexRow, ...] = Field(default_factory=tuple)


class GetTotalReturnIndexTool(BaseTool):
    """`get_total_return_index` — wraps `Capability.TOTAL_RETURN_INDEX` (TAIEX/TPEx 報酬指數;
    `id` is the pseudo-code `TAIEX`/`TPEx`, not a tradable stock)."""

    name = "get_total_return_index"
    description = "Get the TAIEX/TPEx total-return index series (id is the pseudo-code TAIEX/TPEx)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetTotalReturnIndexOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "total_return_index"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TOTAL_RETURN_INDEX,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TotalReturnIndexRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTotalReturnIndexOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTenYearPriceOutput(_RowsOutput):
    rows: tuple[TenYearPriceRow, ...] = Field(default_factory=tuple)


class GetTenYearPriceTool(BaseTool):
    """`get_ten_year_price` — wraps `Capability.TEN_YEAR_PRICE` (十年價線)."""

    name = "get_ten_year_price"
    description = "Get the ten-year price-line close series for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetTenYearPriceOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "ten_year_price"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TEN_YEAR_PRICE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TenYearPriceRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTenYearPriceOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetIntradayKbarOutput(_RowsOutput):
    rows: tuple[KBarRow, ...] = Field(default_factory=tuple)


class GetIntradayKbarTool(BaseTool):
    """`get_intraday_kbar` — wraps `Capability.INTRADAY_KBAR` (minute-bar series; single
    trading day per request upstream)."""

    name = "get_intraday_kbar"
    description = (
        "Get the minute-bar (K-bar) series for one stock, one trading day at a time (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetIntradayKbarOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "intraday_kbar"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INTRADAY_KBAR,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=KBarRow,
            key="bars",
            params=typed.date_params(),
        )
        return GetIntradayKbarOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetPriceMultiTfOutput(_RowsOutput):
    weekly: tuple[PeriodPriceRow, ...] = Field(default_factory=tuple)
    monthly: tuple[PeriodPriceRow, ...] = Field(default_factory=tuple)


class GetPriceMultiTfTool(BaseTool):
    """`get_price_multi_tf` — bundles `Capability.PRICE_WEEKLY` + `Capability.PRICE_MONTHLY`
    (週/月 K bars) into one report-shaped tool rather than two near-duplicate ones, per this
    registry's own bundling convention."""

    name = "get_price_multi_tf"
    description = "Get weekly and monthly bar series for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetPriceMultiTfOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "price_multi_tf"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        weekly_result, weekly_rows = await fetch_rows(
            self._router,
            Capability.PRICE_WEEKLY,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=PeriodPriceRow,
            key="bars",
            params=typed.date_params(),
        )
        _monthly_result, monthly_rows = await fetch_rows(
            self._router,
            Capability.PRICE_MONTHLY,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=PeriodPriceRow,
            key="bars",
            params=typed.date_params(),
        )
        return GetPriceMultiTfOutput(
            source_id=weekly_result.source_id,
            currentness_caveat=weekly_result.currentness_caveat,
            health_status=weekly_result.health.status,
            weekly=weekly_rows,
            monthly=monthly_rows,
        )


class GetIndexSnapshotsOutput(_RowsOutput):
    rows: tuple[Index5sSnapshotRow, ...] = Field(default_factory=tuple)


class GetIndexSnapshotsTool(BaseTool):
    """`get_index_snapshots` — wraps `Capability.INDEX_5S_SNAPSHOT` (5-second index snapshot;
    no `data_id` filter is documented, the full set of index readings comes back)."""

    name = "get_index_snapshots"
    description = (
        "Get the whole-market 5-second index snapshot readings for one trading day (Sponsor-tier)."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetIndexSnapshotsOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "index_snapshots"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INDEX_5S_SNAPSHOT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=Index5sSnapshotRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetIndexSnapshotsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTradingHaltsOutput(_RowsOutput):
    suspensions: tuple[TradingSuspensionRow, ...] = Field(default_factory=tuple)


class GetTradingHaltsTool(BaseTool):
    """`get_trading_halts` — wraps `Capability.TRADING_SUSPENSION` (暫停交易 announcement
    stream). `DAY_TRADING_SUSPENSION` (先賣後買當沖 suspension) has its own sibling tool,
    `GetDayTradingSuspensionTool` below — same shape, distinct dataset, kept separate since the
    two announcement streams cover different suspension reasons."""

    name = "get_trading_halts"
    description = "Get 暫停交易 (trading-halt) announcement rows for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetTradingHaltsOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TRADING_SUSPENSION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TradingSuspensionRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTradingHaltsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            suspensions=rows,
        )


class GetDayTradingSuspensionOutput(_RowsOutput):
    suspensions: tuple[DayTradingSuspensionRow, ...] = Field(default_factory=tuple)


class GetDayTradingSuspensionTool(BaseTool):
    """`get_day_trading_suspension` — wraps `Capability.DAY_TRADING_SUSPENSION` (暫停先賣後買
    當沖 announcement stream) — the sell-first-day-trading-specific sibling of
    `GetTradingHaltsTool`'s general 暫停交易 stream."""

    name = "get_day_trading_suspension"
    description = (
        "Get 暫停先賣後買當沖 (sell-first day-trading suspension) announcement rows for one "
        "stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDayTradingSuspensionOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.DAY_TRADING_SUSPENSION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DayTradingSuspensionRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetDayTradingSuspensionOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            suspensions=rows,
        )


class GetPriceLimitOutput(_RowsOutput):
    rows: tuple[PriceLimitRow, ...] = Field(default_factory=tuple)


class GetPriceLimitTool(BaseTool):
    """`get_price_limit` — wraps `Capability.PRICE_LIMIT` (漲跌停價 daily reference/limit
    price)."""

    name = "get_price_limit"
    description = "Get the daily reference/limit-up/limit-down price rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetPriceLimitOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "price_limit"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.PRICE_LIMIT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=PriceLimitRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetPriceLimitOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table — every tool here is deferred."""
    return [
        (GetStockRosterTool(router), False),
        (GetWarrantUnderlyingTool(router), False),
        (GetTradingCalendarTool(router), False),
        (GetPriceTickTool(router), False),
        (GetValuationRatiosTool(router), False),
        (GetOrderBookStatsTool(router), False),
        (GetTaiexIndexSnapshotTool(router), False),
        (GetDayTradingTool(router), False),
        (GetTotalReturnIndexTool(router), False),
        (GetTenYearPriceTool(router), False),
        (GetIntradayKbarTool(router), False),
        (GetPriceMultiTfTool(router), False),
        (GetIndexSnapshotsTool(router), False),
        (GetTradingHaltsTool(router), False),
        (GetDayTradingSuspensionTool(router), False),
        (GetPriceLimitTool(router), False),
    ]


__all__ = [
    "GetDayTradingOutput",
    "GetDayTradingSuspensionOutput",
    "GetDayTradingSuspensionTool",
    "GetDayTradingTool",
    "GetIndexSnapshotsOutput",
    "GetIndexSnapshotsTool",
    "GetIntradayKbarOutput",
    "GetIntradayKbarTool",
    "GetOrderBookStatsOutput",
    "GetOrderBookStatsTool",
    "GetPriceLimitOutput",
    "GetPriceLimitTool",
    "GetPriceMultiTfOutput",
    "GetPriceMultiTfTool",
    "GetPriceTickOutput",
    "GetPriceTickTool",
    "GetStockRosterOutput",
    "GetStockRosterTool",
    "GetTaiexIndexSnapshotOutput",
    "GetTaiexIndexSnapshotTool",
    "GetTenYearPriceOutput",
    "GetTenYearPriceTool",
    "GetTotalReturnIndexOutput",
    "GetTotalReturnIndexTool",
    "GetTradingCalendarOutput",
    "GetTradingCalendarTool",
    "GetTradingHaltsOutput",
    "GetTradingHaltsTool",
    "GetValuationRatiosOutput",
    "GetValuationRatiosTool",
    "GetWarrantUnderlyingOutput",
    "GetWarrantUnderlyingTool",
    "ValuationBandSummary",
    "build_registration_table",
]
