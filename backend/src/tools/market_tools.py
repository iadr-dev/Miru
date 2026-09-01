"""Per-capability market-data tools — the FinMind/yfinance/TWSE-backed capabilities that have
a real adapter today (`get_price_quote`, `get_dividend_schedule`, `get_institutional_flow`,
`get_company_profile`) plus the sponsor-tier worked example (`get_chip_flows`).

Every tool here validates the router's `SourceResult.payload` into one of
`schemas.capabilities`' normalized per-capability models before returning it — never the raw
dict. Those models' field names were designed (Phase 1/2) to match the adapters' own row-mapper
output 1:1, so `model_validate` on each row is a straight typed pass-through, not a lossy
reshape.
"""

from __future__ import annotations

import asyncio
from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta
from enum import IntEnum, StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.fundamentals import (
    compute_fundamental_metrics,
    compute_revenue_yoy,
    load_symbol_financials,
    load_symbol_market_value,
    load_symbol_month_revenue,
)
from analytics.symbol_universe import is_scoreable_symbol
from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import (
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    PRICE_EOD_JOB,
    SHAREHOLDING_DISTRIBUTION_JOB,
    STOCK_INFO_JOB,
)
from ingest.store import ParquetStore
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    ChipBrokerRow,
    DividendEvent,
    EODBar,
    InstitutionalFlowRow,
    MarketValueRow,
    MarketValueWeightRow,
    SectorClassification,
    StockInfoRow,
    ValuationRatiosRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools._rows import validate_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput, SymbolInput

# The untyped edges every `cast(...)` below narrows a `SourceResult.payload` value from — PEP-695
# local aliases instead of repeating the same cast-target strings at every call site.
type _RowList = list[dict[str, object]]
type _UntypedDict = dict[str, object]
type _OptionalStr = str | None


def _latest_bar(payload: dict[str, object]) -> EODBar | None:
    """`price_eod` payload is `{"bars": [...]}` (FinMind, possibly many rows) or `{"bar": {...}}`
    (TWSE OpenAPI, single latest snapshot) — normalize both shapes to "the latest bar"."""
    bars = payload.get("bars")
    if isinstance(bars, list) and bars:
        rows = cast(_RowList, bars)
        return EODBar.model_validate(rows[-1])
    bar = payload.get("bar")
    if isinstance(bar, dict):
        return EODBar.model_validate(cast(_UntypedDict, bar))
    return None


class GetPriceQuoteOutput(BaseModel):
    """The basic price-quote read (part of the immediate-core set)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    bar: EODBar | None = None


class GetPriceQuoteTool(BaseTool):
    """`get_price_quote` — wraps `Capability.PRICE_EOD` directly via the router (not the full
    multi-capability canonical build — this is the narrow "just the quote" read)."""

    name = "get_price_quote"
    description = "Get the latest available end-of-day price bar for one stock/ETF symbol."
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetPriceQuoteOutput
    card_type: str | None = "price_quote"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.PRICE_EOD, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        return GetPriceQuoteOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            bar=_latest_bar(result.payload),
        )


class GetDividendScheduleOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    events: tuple[DividendEvent, ...] = Field(default_factory=tuple)


class GetDividendScheduleTool(BaseTool):
    """`get_dividend_schedule` — wraps `Capability.DIVIDENDS` (the per-stock/ETF
    除權息/配息 timeline). Public, deferred."""

    name = "get_dividend_schedule"
    description = "Get the announced dividend/ex-dividend schedule for one stock/ETF symbol."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDividendScheduleOutput
    card_type: str | None = "dividend_schedule"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result = await self._router.fetch(
            Capability.DIVIDENDS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            **typed.date_params(),
        )
        raw_events = result.payload.get("events")
        events: tuple[DividendEvent, ...] = ()
        if isinstance(raw_events, list):
            rows = cast(_RowList, raw_events)
            events = tuple(DividendEvent.model_validate(row) for row in rows)
        return GetDividendScheduleOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            events=events,
        )


class InstitutionalFlowRollingNet(BaseModel):
    """One investor category's pre-summed net buy/sell (`sum(buy - sell)`) over the trailing
    5/10/20-trading-day windows within whatever range `get_institutional_flow` fetched — see
    that tool's own docstring for why this exists: without it, a skill asking for a "5/10/20
    日買賣超趨勢" forces the model to sum 20+ raw daily rows by hand across every investor
    category, which measurably happened live (a `chip-flow-review` turn's reasoning trace
    showed it manually adding up 20 days x 3 categories and running out of response budget
    before writing any prose at all — the final answer had zero analysis, just a raw table).
    A window field is `None` when fewer trading days than that window exist in the fetched
    range (e.g. a 5-day request can't back a 20-day figure) — never a partial-window number
    silently mislabeled as the full window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    investor: str
    net_5d: float | None = None
    net_10d: float | None = None
    net_20d: float | None = None


class GetInstitutionalFlowOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    # `rolling_net` is declared BEFORE `rows` — `agent.loop._render_result_for_model` serializes
    # this model to JSON in field-declaration order and hard-truncates at a fixed char budget;
    # with `rows` first, a large window's raw rows alone can consume the entire budget and cut
    # `rolling_net` out of what the model ever sees (confirmed live — see `_rolling_net_by_investor`
    # and `ChipBrokerNetSummary`'s docstrings for the incident this fixes). Declaring the
    # pre-aggregated summary first guarantees it always survives truncation.
    rolling_net: tuple[InstitutionalFlowRollingNet, ...] = Field(default_factory=tuple)
    rows: tuple[InstitutionalFlowRow, ...] = Field(default_factory=tuple)


def _rolling_net_by_investor(
    rows: Sequence[InstitutionalFlowRow],
) -> tuple[InstitutionalFlowRollingNet, ...]:
    """Pre-sum `buy - sell` over the trailing 5/10/20 TRADING days present in `rows` (not
    calendar days — a distinct-`trade_date` count, so a window with holidays/gaps still means
    "N trading sessions"), independently per investor category. Every category the rows
    actually contain gets its own entry — never hardcoded to just foreign/trust/dealer, since
    FinMind's own category set includes `Dealer_Hedging`/`Foreign_Dealer_Self` too (confirmed
    live) and a caller should see whatever categories were really returned."""
    by_investor: dict[str, dict[date_, tuple[int, int]]] = {}
    for row in rows:
        per_date = by_investor.setdefault(row.investor, {})
        buy, sell = per_date.get(row.trade_date, (0, 0))
        per_date[row.trade_date] = (buy + row.buy, sell + row.sell)

    results: list[InstitutionalFlowRollingNet] = []
    for investor, per_date in sorted(by_investor.items()):
        ordered_dates = sorted(per_date.keys())
        results.append(
            InstitutionalFlowRollingNet(
                investor=investor,
                net_5d=_net_over_trailing(per_date, ordered_dates, 5),
                net_10d=_net_over_trailing(per_date, ordered_dates, 10),
                net_20d=_net_over_trailing(per_date, ordered_dates, 20),
            )
        )
    return tuple(results)


def _net_over_trailing(
    per_date: dict[date_, tuple[int, int]], ordered_dates: list[date_], window: int
) -> float | None:
    if len(ordered_dates) < window:
        return None
    trailing = ordered_dates[-window:]
    return float(sum(per_date[d][0] - per_date[d][1] for d in trailing))


class GetInstitutionalFlowTool(BaseTool):
    """`get_institutional_flow` — wraps `Capability.INSTITUTIONAL_FLOW` (三大法人買賣). Public,
    deferred. Also returns `rolling_net` — server-side pre-summed 5/10/20-trading-day net
    buy/sell per investor category (see `InstitutionalFlowRollingNet`'s own docstring for why:
    a skill citing a rolling-window trend must never make the model sum raw daily rows by
    hand)."""

    name = "get_institutional_flow"
    description = (
        "Get the three-major-institutional-investor (foreign/investment-trust/dealer) "
        "buy/sell rows for one stock/ETF symbol over an optional date range, plus a "
        "pre-computed 5/10/20-trading-day rolling net buy/sell per investor category — cite "
        "`rolling_net`'s figures directly rather than summing `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetInstitutionalFlowOutput
    card_type: str | None = "institutional_flow"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result = await self._router.fetch(
            Capability.INSTITUTIONAL_FLOW,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            **typed.date_params(),
        )
        raw_rows = result.payload.get("rows")
        rows: tuple[InstitutionalFlowRow, ...] = ()
        if isinstance(raw_rows, list):
            typed_rows = cast(_RowList, raw_rows)
            rows = tuple(InstitutionalFlowRow.model_validate(row) for row in typed_rows)
        return GetInstitutionalFlowOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            rolling_net=_rolling_net_by_investor(rows),
        )


class ChipBrokerNetSummary(BaseModel):
    """One broker/branch's total net buy/sell (`sum(buy - sell)`) over the whole fetched range —
    see `_top_broker_net_summary`'s docstring for why this exists: the same architecture gap as
    `InstitutionalFlowRollingNet` (Finding #69), one tool over. `get_chip_flows` can return
    thousands of per-day per-branch rows (a live repro against 鴻海/2317 returned 6,500+), and
    without a pre-aggregated summary the model was observed spending its entire response budget
    manually eyeballing/summing individual 分點 rows, leaving zero room for prose — this field
    is what it should cite instead."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_id: str
    broker_name: str
    net: int


class GetChipFlowsOutput(BaseModel):
    """Sponsor-tier 分點 branch trading rows. Returned as-is by `run()` — `dispatch_tool` (not
    this class) is what substitutes the `MaskedTeaser` for an unentitled caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    # `top_brokers` declared BEFORE `rows` for the same reason as `InstitutionalFlowOutput`'s
    # `rolling_net` field ordering — see that field's comment. `rows` here is the worse offender
    # in practice: a live repro against 鴻海/2317 returned 10,000+ rows, guaranteeing `rows`
    # alone exceeds `agent.loop._MAX_MODEL_RESULT_CHARS` and would silently drop `top_brokers`
    # from what the model ever sees if it were declared second.
    top_brokers: tuple[ChipBrokerNetSummary, ...] = Field(default_factory=tuple)
    rows: tuple[ChipBrokerRow, ...] = Field(default_factory=tuple)


_TOP_BROKER_COUNT = 10


def _top_broker_net_summary(rows: Sequence[ChipBrokerRow]) -> tuple[ChipBrokerNetSummary, ...]:
    """Pre-sum `buy - sell` per broker/branch across every row in the fetched range, then keep
    only the `_TOP_BROKER_COUNT` largest-magnitude net positions (both buy- and sell-side) —
    the handful of branches that actually moved the stock, not a re-derivation of all 6,500+ raw
    rows the model would otherwise have to scan itself."""
    net_by_broker: dict[str, tuple[str, int]] = {}
    for row in rows:
        name, net = net_by_broker.get(row.broker_id, (row.broker_name, 0))
        net_by_broker[row.broker_id] = (name, net + row.buy - row.sell)

    ranked = sorted(net_by_broker.items(), key=lambda item: abs(item[1][1]), reverse=True)
    return tuple(
        ChipBrokerNetSummary(broker_id=broker_id, broker_name=name, net=net)
        for broker_id, (name, net) in ranked[:_TOP_BROKER_COUNT]
    )


class GetChipFlowsTool(BaseTool):
    """`get_chip_flows` — wraps `Capability.CHIP_BROKER` (FinMind Sponsor-tier 分點進出).

    The sponsor-tier worked example: this tool's own `run()` has no entitlement logic at all —
    it always fetches and returns real data. Gating happens exactly once, in
    `tools.base.dispatch_tool`, which checks `data_class` before ever calling `run()`.
    """

    name = "get_chip_flows"
    description = (
        "Get branch(分點)/broker-level buy-sell trading rows for one stock (Sponsor-tier), plus "
        "a pre-computed `top_brokers` summary of the largest net buy/sell positions across the "
        "whole range — cite `top_brokers` directly rather than summing `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetChipFlowsOutput
    card_type: str | None = "chip_flows"
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result = await self._router.fetch(
            Capability.CHIP_BROKER,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            **typed.date_params(),
        )
        raw_rows = result.payload.get("rows")
        rows: tuple[ChipBrokerRow, ...] = ()
        if isinstance(raw_rows, list):
            typed_rows = cast(_RowList, raw_rows)
            rows = tuple(ChipBrokerRow.model_validate(row) for row in typed_rows)
        return GetChipFlowsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            top_brokers=_top_broker_net_summary(rows),
        )


_ETF_REPORT_LOOKBACK_DAYS = 10  # bounded per-symbol window to find "target day + prior day"
_MONEY_FLOW_MAX_WINDOW_DAYS = 30  # bounded date range for the cross-ETF aggregation read
_PREMIUM_DISCOUNT_UNAVAILABLE_REASON = (
    "No NAV/IOPV adapter is wired for premium/discount (折溢價) — the Active-ETF Holding and "
    "HoldingChange datasets carry constituent-level shares/weight/market-value only, never the "
    "fund's own NAV vs market-price comparison; this field is never fabricated in its place."
)
_ESTIMATED_NAV_CAVEAT = (
    "估算淨值 = 持股市值(持股張數 x 當日收盤價) ÷ 發行單位數，"
    "未計入現金、應收股利、應計費用或匯率，且以收盤價估算，"
    "非發行商公告 NAV 亦非即時 IOPV；折溢價亦為此估算值與市價之比較，僅供參考。"
)


class HoldingStatus(StrEnum):
    """The derived per-holding classification layered on top of FinMind's raw buy/sell change
    row — never stored by FinMind itself, computed here from (buy, sell, prior-day weight)."""

    NEW = "新增"
    REMOVED = "刪除"
    INCREASED = "加碼"
    DECREASED = "減碼"
    NO_PRIOR_DATA = "no_prior_data"  # e.g. the very first ingested day for this ETF


def _classify_holding_status(
    *,
    buy: int,
    sell: int,
    prior_weight: float | None,
    current_weight: float | None,
    has_prior_day: bool,
) -> HoldingStatus:
    """Buy/sell come from FinMind's own pre-diffed `HoldingChange` row (exactly one is
    non-zero); this only adds the new-entry/closed-position distinction a raw share-count
    delta can't express on its own."""
    if not has_prior_day:
        return HoldingStatus.NO_PRIOR_DATA
    if prior_weight is None and buy > 0:
        return HoldingStatus.NEW
    if current_weight is None and sell > 0:
        return HoldingStatus.REMOVED
    if buy > 0:
        return HoldingStatus.INCREASED
    return HoldingStatus.DECREASED


def _etf_report_window(trade_date: date_ | None) -> tuple[str, str]:
    """A bounded per-symbol lookback window wide enough to contain a target day and its
    immediately-prior trading day, without ever requesting unbounded history."""
    end = trade_date or date_.today()
    start = end - timedelta(days=_ETF_REPORT_LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()


def _resolve_report_date(dates: list[date_], requested: date_ | None) -> date_:
    """The latest date actually present at/before `requested` (or the latest present date at
    all, if `requested` is `None` — the "latest" case)."""
    if requested is None:
        return dates[-1]
    eligible = [d for d in dates if d <= requested]
    return eligible[-1] if eligible else dates[-1]


def _rows_from_store[T: BaseModel](model: type[T], raw_rows: list[dict[str, object]]) -> list[T]:
    """`ParquetStore.read_partition`/`read_range` hand back DuckDB's own read-back of a
    Hive-partitioned glob, which resurfaces the partition-key columns (`dataset`/`date`/`v`)
    alongside the written row data — those are storage bookkeeping, not part of any
    `schemas.capabilities` contract, so they are dropped before validating against a
    `extra="forbid"` model rather than relaxing that model's strictness."""
    field_names = set(model.model_fields)
    return [
        model.model_validate({k: v for k, v in row.items() if k in field_names}) for row in raw_rows
    ]


def _prior_report_date(dates: list[date_], target: date_) -> date_ | None:
    """The date immediately before `target` within the fetched window — `None` if `target` is
    the earliest date the window returned (treated as "no prior data", never guessed)."""
    idx = dates.index(target)
    return dates[idx - 1] if idx > 0 else None


class GetActiveEtfReportInput(SymbolInput):
    """One active ETF + an optional trading day; omitted/`None` means "latest available"."""

    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report on (ISO date). Omitted or null means the latest "
        "available trading day.",
    )


class ActiveEtfReportRow(BaseModel):
    """One constituent's row in the 操作日報 — only constituents with a nonzero buy/sell that
    day appear (FinMind's `HoldingChange` is already filtered to changed rows)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_stock_id: str
    component_stock_name: str
    status: HoldingStatus
    shares_delta: int  # buy - sell for the day (FinMind's own pre-diffed change)
    pct_change: float | None = None  # shares_delta / prior-day shares, when the prior row exists
    current_weight: float | None = None
    weight_delta_pct: float | None = None  # current_weight - prior_weight, when both exist


class GetActiveEtfReportOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    symbol: str
    market: str
    report_date: date_ | None = None
    prior_date: date_ | None = None
    fund_size: float | None = None  # 基金規模 — sum of constituent market_value on report_date
    fund_size_prior: float | None = None
    fund_size_change_pct: float | None = None  # 基金規模較前日
    premium_discount_pct: float | None = None  # 折溢價 (official NAV) — always None, see reason
    premium_discount_unavailable_reason: str | None = _PREMIUM_DISCOUNT_UNAVAILABLE_REASON
    # 估算淨值/折溢價 — self-computed from holdings x close ÷ units_outstanding, NOT official NAV;
    # carries _ESTIMATED_NAV_CAVEAT whenever either is non-None. None when units_outstanding or
    # the ETF's own close is unavailable (never fabricated).
    estimated_nav_per_unit: float | None = None
    estimated_premium_discount_pct: float | None = None
    estimation_caveat: str | None = None
    rows: tuple[ActiveEtfReportRow, ...] = Field(default_factory=tuple)


def _active_etf_report_row(
    change: ActiveEtfHoldingChangeRow,
    *,
    prior_row: ActiveEtfHoldingRow | None,
    current_row: ActiveEtfHoldingRow | None,
    has_prior_day: bool,
) -> ActiveEtfReportRow:
    """One changed constituent's report row — pulled out of `GetActiveEtfReportTool.run` purely
    to keep that method's own branching shallow; identical status/delta computation, just
    named."""
    status = _classify_holding_status(
        buy=change.buy,
        sell=change.sell,
        prior_weight=prior_row.weight if prior_row else None,
        current_weight=current_row.weight if current_row else None,
        has_prior_day=has_prior_day,
    )
    shares_delta = change.buy - change.sell
    pct_change = shares_delta / prior_row.shares if prior_row and prior_row.shares else None
    weight_delta_pct = current_row.weight - prior_row.weight if current_row and prior_row else None
    return ActiveEtfReportRow(
        component_stock_id=change.component_stock_id,
        component_stock_name=change.component_stock_name,
        status=status,
        shares_delta=shares_delta,
        pct_change=pct_change,
        current_weight=current_row.weight if current_row else None,
        weight_delta_pct=weight_delta_pct,
    )


def _active_etf_report_rows(
    changes: tuple[ActiveEtfHoldingChangeRow, ...],
    *,
    prior_holdings: dict[str, ActiveEtfHoldingRow],
    report_holdings: dict[str, ActiveEtfHoldingRow],
    has_prior_day: bool,
) -> list[ActiveEtfReportRow]:
    rows: list[ActiveEtfReportRow] = []
    for change in changes:
        if change.buy == 0 and change.sell == 0:
            continue  # defensive — FinMind documents exactly one non-zero per row
        rows.append(
            _active_etf_report_row(
                change,
                prior_row=prior_holdings.get(change.component_stock_id),
                current_row=report_holdings.get(change.component_stock_id),
                has_prior_day=has_prior_day,
            )
        )
    return rows


def _real_calendar(store: ParquetStore, market: str) -> TradingDayCalendar:
    """The real trading-day calendar for `market`, built from `PRICE_EOD_JOB`'s own
    already-ingested partition dates — same reasoning and construction as
    `ingest/daemon.py::_build_calendar`, duplicated here rather than imported because `tools/`
    may not import `ingest/` (the DEPENDENCY RULE only allows `tools -> context -> dataaccess ->
    sources`; `ingest` is a sibling ingestion-side module, not a lower layer this one may pull
    from). A plain `TradingDayCalendar()` (naive Mon-Fri weekday math) previously used at every
    call site here would miscount real TW holidays as trading days, corrupting the "prior
    trading day" every close-to-close calculation in this module depends on — the same class of
    bug fixed in the ingest daemon's own calendar construction."""
    known_dates = store.list_partition_dates(PRICE_EOD_JOB.dataset, market)
    if not known_dates:
        return TradingDayCalendar()
    return calendar_from_known_dates(known_dates)


def _closes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every real equity/ETF symbol's closing price on `trade_date`, read from the whole-market
    `TaiwanStockPrice` partition the daily ingest job already wrote — the same derived-store
    read `GetEtfMoneyFlowTool` uses, not a per-symbol live fetch (`_active_etf_holding_value`'s
    own docstring explains why this beats trusting FinMind's own `market_value` field).

    Filtered through `analytics.symbol_universe.is_scoreable_symbol`: the raw partition is
    dominated by warrant/derivative codes (~17K/day vs. ~2.65K real equities/ETFs — see that
    module's docstring for the measured split), which would otherwise inflate every
    breadth/contribution/heatmap calculation built from this map with thousands of warrant rows
    that don't belong in "how many real stocks moved today"."""
    raw_bars = store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
    closes: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        close = row.get("close")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol)
            and isinstance(close, (int, float))
        ):
            closes[symbol] = float(close)
    return closes


def _holdings_market_value(
    store: ParquetStore,
    market: str,
    trade_date: date_,
    holdings: dict[str, ActiveEtfHoldingRow],
) -> float | None:
    """Total market value of `holdings` on `trade_date`, as `sum(shares x that day's closing
    price)` — never FinMind's own `ActiveEtfHoldingRow.market_value` field, which its upstream
    API returns as a hardcoded `0.0` for every row of this dataset (confirmed live: every
    `TaiwanStockActiveETFHolding` row FinMind serves carries a real `shares`/`weight`, but
    `market_value: 0.0` unconditionally). Recomputing from the already-ingested
    `TaiwanStockPrice` partition gives `fund_size` a real, non-zero number instead of faithfully
    propagating a broken upstream field. `None` when no constituent's close was found in the
    store (a genuinely missing price is not silently treated as a $0 fund), not when `holdings`
    is merely empty (`sum` of nothing is legitimately `0`, but that case never reaches here —
    the caller only calls this when `holdings` is non-empty)."""
    closes = _closes_by_symbol(store, market, trade_date)
    values = [
        h.shares * closes[h.component_stock_id]
        for h in holdings.values()
        if h.component_stock_id in closes
    ]
    return sum(values) if values else None


def _active_etf_fund_size_fields(
    store: ParquetStore,
    market: str,
    dates: tuple[date_, date_ | None],
    holdings: tuple[dict[str, ActiveEtfHoldingRow], dict[str, ActiveEtfHoldingRow]],
) -> tuple[float | None, float | None, float | None]:
    """`(fund_size, fund_size_prior, fund_size_change_pct)` — pulled out of
    `GetActiveEtfReportTool.run` for the same shallow-branching reason as the row-builders.
    `dates`/`holdings` are each a `(report, prior)` pair, kept paired in one parameter rather
    than four separate ones to stay under the project's argument-count lint budget."""
    report_date, prior_date = dates
    report_holdings, prior_holdings = holdings
    fund_size = (
        _holdings_market_value(store, market, report_date, report_holdings)
        if report_holdings
        else None
    )
    fund_size_prior = (
        _holdings_market_value(store, market, prior_date, prior_holdings)
        if prior_holdings and prior_date is not None
        else None
    )
    fund_size_change_pct = (
        (fund_size - fund_size_prior) / fund_size_prior
        if fund_size is not None and fund_size_prior
        else None
    )
    return fund_size, fund_size_prior, fund_size_change_pct


def _estimated_nav_fields(
    store: ParquetStore,
    etf_symbol: Symbol,
    report_date: date_,
    derived: tuple[float | None, int | None],
) -> tuple[float | None, float | None]:
    """`(estimated_nav_per_unit, estimated_premium_discount_pct)` for an active ETF, self-computed
    from the already-derived `fund_size` (持股市值) ÷ `units_outstanding` (發行單位數) — passed
    paired as `derived=(fund_size, units_outstanding)` to stay under the argument-count budget —
    with the premium/discount taken against the ETF's own closing market price on `report_date`.
    This is an approximation, never the issuer's official NAV/IOPV: it omits cash, receivables,
    accrued fees and FX, and uses EOD close rather than an intraday indicative value — the caller
    stamps `_ESTIMATED_NAV_CAVEAT` whenever a value is returned. Every output is `None` (never
    fabricated) when its inputs are missing: no `fund_size`, no/zero `units_outstanding`, or the
    ETF's own close absent from the ingested `TaiwanStockPrice` partition."""
    fund_size, units_outstanding = derived
    if fund_size is None or not units_outstanding:
        return None, None
    nav_per_unit = fund_size / units_outstanding
    market_price = _closes_by_symbol(store, etf_symbol.market.value, report_date).get(etf_symbol.id)
    premium_discount_pct = (
        market_price / nav_per_unit - 1.0 if market_price is not None and nav_per_unit else None
    )
    return nav_per_unit, premium_discount_pct


class GetActiveEtfReportTool(BaseTool):
    """`get_active_etf_report` — 主動式ETF 操作日報 for one ETF: per-holding 新增/刪除/加碼/減碼 +
    變動張數/幅度 + 目前權重/權重變動% + 基金規模較前日 (折溢價 unavailable, see
    `_PREMIUM_DISCOUNT_UNAVAILABLE_REASON`). Wraps `Capability.ACTIVE_ETF_HOLDING` (the raw daily
    snapshot, for weight/shares) + `Capability.ACTIVE_ETF_HOLDING_CHANGE` (FinMind's own
    pre-diffed buy/sell) via the router — a single-ETF report is always a per-symbol read, never
    the whole-market ingest path. `fund_size` is recomputed from shares x the ingested
    `TaiwanStockPrice` close (see `_active_etf_holding_value`'s docstring for why: FinMind's own
    `market_value` field on this dataset is unconditionally `0.0`), which is why this tool also
    takes the Parquet `store` alongside the router."""

    name = "get_active_etf_report"
    description = (
        "Get the daily operations report (操作日報) for one active-management ETF: per-holding "
        "新增/刪除/加碼/減碼 status, share-count and weight changes vs the prior trading day, plus "
        "fund-size change (Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetActiveEtfReportInput
    output_model: type[BaseModel] = GetActiveEtfReportOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "active_etf_report"

    def __init__(self, router: DataAccessRouter, store: ParquetStore) -> None:
        self._router = router
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetActiveEtfReportInput, args)
        symbol = typed.to_symbol()
        start, end = _etf_report_window(typed.trade_date)

        holding_result = await self._router.fetch(
            Capability.ACTIVE_ETF_HOLDING,
            symbol,
            tls_policy=ctx.tls_policy,
            start_date=start,
            end_date=end,
        )
        holdings = validate_rows(ActiveEtfHoldingRow, holding_result.payload, "holdings")

        if not holdings:
            return GetActiveEtfReportOutput(
                source_id=holding_result.source_id,
                currentness_caveat=holding_result.currentness_caveat,
                health_status=holding_result.health.status,
                symbol=symbol.id,
                market=symbol.market.value,
            )

        dates = sorted({h.trade_date for h in holdings})
        report_date = _resolve_report_date(dates, typed.trade_date)
        prior_date = _prior_report_date(dates, report_date)

        by_date: dict[date_, dict[str, ActiveEtfHoldingRow]] = {}
        for h in holdings:
            by_date.setdefault(h.trade_date, {})[h.component_stock_id] = h
        report_holdings = by_date.get(report_date, {})
        prior_holdings = by_date.get(prior_date, {}) if prior_date is not None else {}

        change_result = await self._router.fetch(
            Capability.ACTIVE_ETF_HOLDING_CHANGE,
            symbol,
            tls_policy=ctx.tls_policy,
            start_date=report_date.isoformat(),
            end_date=report_date.isoformat(),
        )
        changes = validate_rows(ActiveEtfHoldingChangeRow, change_result.payload, "changes")

        rows = _active_etf_report_rows(
            changes,
            prior_holdings=prior_holdings,
            report_holdings=report_holdings,
            has_prior_day=prior_date is not None,
        )
        fund_size, fund_size_prior, fund_size_change_pct = _active_etf_fund_size_fields(
            self._store,
            symbol.market.value,
            (report_date, prior_date),
            (report_holdings, prior_holdings),
        )

        units_outstanding = await self._fetch_units_outstanding(symbol, ctx)
        estimated_nav_per_unit, estimated_premium_discount_pct = _estimated_nav_fields(
            self._store,
            symbol,
            report_date,
            (fund_size, units_outstanding),
        )
        estimation_caveat = (
            _ESTIMATED_NAV_CAVEAT
            if estimated_nav_per_unit is not None or estimated_premium_discount_pct is not None
            else None
        )

        return GetActiveEtfReportOutput(
            source_id=holding_result.source_id,
            currentness_caveat=holding_result.currentness_caveat,
            health_status=holding_result.health.status,
            symbol=symbol.id,
            market=symbol.market.value,
            report_date=report_date,
            prior_date=prior_date,
            fund_size=fund_size,
            fund_size_prior=fund_size_prior,
            fund_size_change_pct=fund_size_change_pct,
            estimated_nav_per_unit=estimated_nav_per_unit,
            estimated_premium_discount_pct=estimated_premium_discount_pct,
            estimation_caveat=estimation_caveat,
            rows=tuple(rows),
        )

    async def _fetch_units_outstanding(self, symbol: Symbol, ctx: ToolContext) -> int | None:
        """`units_outstanding` (發行單位數) from the ETF fund-info slice — the denominator for the
        estimated-NAV computation. A best-effort read: any fetch failure or missing field yields
        `None`, which propagates to `estimated_nav_per_unit=None` rather than raising, so the core
        holdings report is never blocked by the fund-info source being unavailable."""
        try:
            info_result = await self._router.fetch(
                Capability.ETF_HOLDINGS, symbol, tls_policy=ctx.tls_policy
            )
        except Exception:
            return None
        raw_info = info_result.payload.get("info")
        if not isinstance(raw_info, dict):
            return None
        units = cast(_UntypedDict, raw_info).get("units_outstanding")
        return units if isinstance(units, int) else None


class GetEtfMoneyFlowInput(BaseModel):
    """A bounded date range + market scope — never an "all symbols" shape. The aggregation
    itself happens server-side over every active ETF's ingested holdings for the window, not by
    accepting a symbol list to fan out over."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    start_date: date_
    end_date: date_
    top_n: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_bounded_range(self) -> GetEtfMoneyFlowInput:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if (self.end_date - self.start_date).days > _MONEY_FLOW_MAX_WINDOW_DAYS:
            raise ValueError(f"date range must not exceed {_MONEY_FLOW_MAX_WINDOW_DAYS} days")
        return self


class EtfMoneyFlowRow(BaseModel):
    """One constituent stock's cross-ETF aggregation row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_stock_id: str
    component_stock_name: str
    active_etf_holder_count: int | None = None  # 主動式檔數 — None if no holding snapshot found
    held_market_value: float | None = None  # 持有市值 — None if no holding snapshot found
    net_shares_flow: int  # sum(buy - sell) across every active ETF over the window
    buying_etf_count: int  # 買 — distinct ETFs net-buying this stock over the window
    selling_etf_count: int  # 賣 — distinct ETFs net-selling this stock over the window


class GetEtfMoneyFlowOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    start_date: date_
    end_date: date_
    data_available: bool
    unavailable_reason: str | None = None
    inflow_ranking: tuple[EtfMoneyFlowRow, ...] = Field(default_factory=tuple)
    outflow_ranking: tuple[EtfMoneyFlowRow, ...] = Field(default_factory=tuple)


@dataclass
class _StockAgg:
    """Mutable running aggregate for one constituent stock — accumulated across every ETF's
    change rows in the window, never itself a canonical/output type."""

    component_stock_name: str
    net_shares_flow: int = 0
    per_etf_net: dict[str, int] = field(default_factory=dict[str, int])


class GetEtfMoneyFlowTool(BaseTool):
    """`get_etf_money_flow` — cross-ETF 資金流向 aggregated over every active ETF's ingested
    holdings for a bounded date range: 主動式檔數/持有市值 per stock, 流入/流出排行, 多空共識.

    Sourced from the **ingest-owned Parquet store** (`ingest.store.ParquetStore`), not a live
    per-symbol router fan-out: this is a genuinely cross-symbol aggregation (every active ETF's
    holdings, for every day in the window), and the one legitimate whole-market path for that
    shape of read is the store the whole-market ingest job already populates — reading it here
    is not a second whole-market-scan path, it's the same derived-store read every other
    analytics consumer uses. If the ingest job hasn't landed a partition for a requested date
    yet, this tool reports that rather than falling back to an unbounded live fan-out.
    """

    name = "get_etf_money_flow"
    description = (
        "Get the cross-ETF money-flow aggregation over active-management ETF holdings for a "
        "bounded date range: per-stock holder count/held value, inflow/outflow ranking, and "
        "bullish/bearish ETF consensus (Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetEtfMoneyFlowInput
    output_model: type[BaseModel] = GetEtfMoneyFlowOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "etf_money_flow"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEtfMoneyFlowInput, args)
        calendar = _real_calendar(self._store, typed.market.value)
        window_dates = calendar.business_days_between(typed.start_date, typed.end_date)

        raw_changes = self._store.read_range(
            ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, typed.market.value, window_dates
        )
        if not raw_changes:
            return GetEtfMoneyFlowOutput(
                market=typed.market.value,
                start_date=typed.start_date,
                end_date=typed.end_date,
                data_available=False,
                unavailable_reason=(
                    "No ingested active-ETF holding-change partition found for "
                    f"{typed.market.value} in [{typed.start_date.isoformat()}, "
                    f"{typed.end_date.isoformat()}] — the whole-market ingest job may not have "
                    "run yet for this range."
                ),
            )
        changes = _rows_from_store(ActiveEtfHoldingChangeRow, raw_changes)

        aggregates: dict[str, _StockAgg] = {}
        for change in changes:
            agg = aggregates.get(change.component_stock_id)
            if agg is None:
                agg = _StockAgg(component_stock_name=change.component_stock_name)
                aggregates[change.component_stock_id] = agg
            net = change.buy - change.sell
            agg.per_etf_net[change.symbol] = agg.per_etf_net.get(change.symbol, 0) + net
            agg.net_shares_flow += net

        holding_end_date = window_dates[-1] if window_dates else typed.end_date
        raw_holdings = self._store.read_partition(
            ACTIVE_ETF_HOLDING_JOB.dataset, typed.market.value, holding_end_date
        )
        holdings = _rows_from_store(ActiveEtfHoldingRow, raw_holdings)
        holder_counts: dict[str, int] = {}
        held_values: dict[str, float] = {}
        for holding in holdings:
            holder_counts[holding.component_stock_id] = (
                holder_counts.get(holding.component_stock_id, 0) + 1
            )
            held_values[holding.component_stock_id] = (
                held_values.get(holding.component_stock_id, 0.0) + holding.market_value
            )

        rows: list[EtfMoneyFlowRow] = []
        for stock_id, agg in aggregates.items():
            buying = sum(1 for net in agg.per_etf_net.values() if net > 0)
            selling = sum(1 for net in agg.per_etf_net.values() if net < 0)
            rows.append(
                EtfMoneyFlowRow(
                    component_stock_id=stock_id,
                    component_stock_name=agg.component_stock_name,
                    active_etf_holder_count=holder_counts.get(stock_id),
                    held_market_value=held_values.get(stock_id),
                    net_shares_flow=agg.net_shares_flow,
                    buying_etf_count=buying,
                    selling_etf_count=selling,
                )
            )

        inflow = tuple(sorted(rows, key=lambda r: r.net_shares_flow, reverse=True)[: typed.top_n])
        outflow = tuple(sorted(rows, key=lambda r: r.net_shares_flow)[: typed.top_n])

        return GetEtfMoneyFlowOutput(
            market=typed.market.value,
            start_date=typed.start_date,
            end_date=typed.end_date,
            data_available=True,
            inflow_ranking=inflow,
            outflow_ranking=outflow,
        )


_INDEX_CONTRIBUTION_TOP_N = 10
_CONTRIBUTION_CAVEAT = (
    "Estimated from market-cap weight x price change; not TWSE's official float-adjusted "
    "index-contribution figure — this codebase has no float-adjusted-divisor data."
)


def _latest_dataset_date(store: ParquetStore, dataset: str, market: str) -> date_ | None:
    """The newest date `dataset`+`market` has an actually-written partition for, found by
    scanning the Hive-partitioned directory tree — mirrors
    `analytics.screener.store._latest_partition_date`'s own scan, generalized to any
    whole-market dataset rather than just `screener_flags`. Never assumes "today" has landed;
    the daily ingest job may not have run yet, so "latest" always means "latest actually
    written", never a naive `date.today()`."""
    market_dir = store.root / f"dataset={dataset}" / f"market={market}"
    if not market_dir.exists():
        return None
    candidates: list[date_] = []
    for child in market_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("date="):
            continue
        try:
            day = date_.fromisoformat(child.name.removeprefix("date="))
        except ValueError:
            continue
        if store.has_partition(dataset, market, day):
            candidates.append(day)
    return max(candidates) if candidates else None


def _two_day_window(
    store: ParquetStore,
    dataset: str,
    market: str,
    calendar: TradingDayCalendar,
    requested: date_ | None,
) -> tuple[date_, date_] | None:
    """Resolve `(trade_date, prior_trade_date)` against `dataset`+`market`: `trade_date` is
    `requested` if given, else the latest partition this dataset actually has written;
    `prior_trade_date` is the trading day immediately before it. Returns `None` if either
    day's partition is missing — the caller reports `data_available=False` rather than
    fabricating a window the ingest job hasn't actually landed yet."""
    trade_date = requested or _latest_dataset_date(store, dataset, market)
    if trade_date is None or not store.has_partition(dataset, market, trade_date):
        return None
    prior_date = calendar.shift_business_days(trade_date, -1)
    if not store.has_partition(dataset, market, prior_date):
        return None
    return trade_date, prior_date


class GetAdvanceDeclineInput(BaseModel):
    """A market + an optional trading day; omitted/`None` means "latest available"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report breadth for (ISO date). Omitted or null means the "
        "latest available trading day.",
    )


class AdvanceDeclineBuckets(BaseModel):
    """Symbol counts bucketed by close-to-close percentage change. `unchanged` is exactly
    `pct_change == 0`; every other symbol falls into exactly one up- or down-side bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    up_over_5pct: int = 0
    up_3_to_5pct: int = 0
    up_2_to_3pct: int = 0
    up_1_to_2pct: int = 0
    up_0_to_1pct: int = 0
    unchanged: int = 0
    down_0_to_1pct: int = 0
    down_1_to_2pct: int = 0
    down_2_to_3pct: int = 0
    down_3_to_5pct: int = 0
    down_over_5pct: int = 0


class GetAdvanceDeclineOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    advancing_count: int = 0
    declining_count: int = 0
    unchanged_count: int = 0
    buckets: AdvanceDeclineBuckets = Field(default_factory=AdvanceDeclineBuckets)


_BUCKET_THRESHOLD_5PCT = 5
_BUCKET_THRESHOLD_3PCT = 3
_BUCKET_THRESHOLD_2PCT = 2
_BUCKET_THRESHOLD_1PCT = 1


def _advance_decline_bucket_name(pct: float) -> str:
    """Which `AdvanceDeclineBuckets` field one symbol's close-to-close percentage change
    (already `x100` — a whole-number percent, not a fraction) belongs in."""
    if pct == 0:
        return "unchanged"
    magnitude, sign = abs(pct), 1 if pct > 0 else -1
    if magnitude > _BUCKET_THRESHOLD_5PCT:
        step = "over_5pct"
    elif magnitude > _BUCKET_THRESHOLD_3PCT:
        step = "3_to_5pct"
    elif magnitude > _BUCKET_THRESHOLD_2PCT:
        step = "2_to_3pct"
    elif magnitude > _BUCKET_THRESHOLD_1PCT:
        step = "1_to_2pct"
    else:
        step = "0_to_1pct"
    return f"{'up' if sign > 0 else 'down'}_{step}"


def _unavailable_reason_for_window(
    dataset_label: str, market: str, trade_date: date_ | None
) -> str:
    """The shared "no partition (yet)" message every two-day-window tool in this module
    surfaces when `_two_day_window` returns `None` — one wording, not one per tool."""
    when = f" on {trade_date.isoformat()}" if trade_date else ""
    return (
        f"No ingested {dataset_label} partition (and/or its prior trading day) found for "
        f"{market}{when} — the whole-market ingest job may not have run yet for this date."
    )


class GetAdvanceDeclineTool(BaseTool):
    """`get_advance_decline` — whole-market advance/decline breadth for one trading day: every
    symbol's close-to-close percentage change, bucketed into >5%/3-5%/2-3%/1-2%/0-1% on both
    the up- and down-side plus `unchanged`, read from the already-ingested `PRICE_EOD_JOB`
    Parquet partition (the same derived-store read `_closes_by_symbol` backs for
    `GetActiveEtfReportTool`) — never a per-symbol live fan-out, since this is a genuinely
    whole-market aggregate and reading the ingest-owned store back is the one legitimate path
    for that shape of read (see `GetEtfMoneyFlowTool`'s own docstring for the same reasoning).

    Public tier: this reads `Capability.PRICE_EOD`'s own already-public whole-market dataset
    (`sources/finmind/technical.py`'s `_DatasetConfig.data_class` for `PRICE_EOD` is `PUBLIC`),
    not a Sponsor-gated one — unlike `get_index_contribution` alongside it, which reads a
    genuinely Sponsor-tier dataset and is gated accordingly."""

    name = "get_advance_decline"
    description = (
        "Get the whole-market advance/decline breadth for one trading day (defaults to the "
        "latest available day): counts of advancing/declining/unchanged symbols, bucketed by "
        "close-to-close percentage change."
    )
    input_model: type[BaseModel] = GetAdvanceDeclineInput
    output_model: type[BaseModel] = GetAdvanceDeclineOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "advance_decline"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetAdvanceDeclineInput, args)
        market = typed.market.value
        calendar = _real_calendar(self._store, market)
        window = _two_day_window(
            self._store, PRICE_EOD_JOB.dataset, market, calendar, typed.trade_date
        )
        if window is None:
            return GetAdvanceDeclineOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        today_closes = _closes_by_symbol(self._store, market, trade_date)
        prior_closes = _closes_by_symbol(self._store, market, prior_date)

        bucket_counts: dict[str, int] = {}
        advancing = declining = unchanged = 0
        for symbol, close in today_closes.items():
            prior_close = prior_closes.get(symbol)
            if not prior_close:  # missing or legitimately-zero prior close — never divide by it
                continue
            pct = (close - prior_close) / prior_close * 100
            if pct > 0:
                advancing += 1
            elif pct < 0:
                declining += 1
            else:
                unchanged += 1
            bucket = _advance_decline_bucket_name(pct)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        return GetAdvanceDeclineOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            advancing_count=advancing,
            declining_count=declining,
            unchanged_count=unchanged,
            buckets=AdvanceDeclineBuckets(**bucket_counts),
        )


_MOMENTUM_TOP_N = 10
# Trading-day lookback per period — never a calendar-day offset (`TradingDayCalendar.
# shift_business_days`). "month" is 21 trading days (~1 calendar month at 5 sessions/week),
# the same convention `analytics/score.py`'s own trend-quality window uses.
_MOMENTUM_PERIOD_TRADING_DAYS: dict[str, int] = {"day": 1, "week": 5, "month": 21}


class MomentumPeriod(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class GetPriceMomentumInput(BaseModel):
    """A market + a lookback period + an optional trading day (the window's end, defaults to
    latest available) + a top-N cap — mirrors `GetIndexContributionInput`'s date/top_n shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    period: MomentumPeriod = Field(
        default=MomentumPeriod.WEEK,
        description="Lookback window: 'day' (1 trading day), 'week' (5 trading days), or "
        "'month' (21 trading days).",
    )
    trade_date: date_ | None = Field(
        default=None,
        description="End of the momentum window (ISO date). Omitted or null means the latest "
        "available trading day.",
    )
    top_n: int = Field(default=_MOMENTUM_TOP_N, ge=1, le=50)


class PriceMomentumRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    pct_change: float  # (close - period_ago_close) / period_ago_close x 100, a whole percent


class GetPriceMomentumOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    period: MomentumPeriod = MomentumPeriod.WEEK
    trade_date: date_ | None = None
    period_ago_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    top_gainers: tuple[PriceMomentumRow, ...] = Field(default_factory=tuple)
    top_losers: tuple[PriceMomentumRow, ...] = Field(default_factory=tuple)


class GetPriceMomentumTool(BaseTool):
    """`get_price_momentum` — 本日/本週/本月強勢股/弱勢股: pure close-to-close percentage change
    over a caller-chosen trading-day lookback (1/5/21 trading days — never a calendar-day
    offset, `TradingDayCalendar.shift_business_days`), ranked independently from
    `get_score_leaderboard`'s multi-factor composite score. Where the leaderboard blends
    momentum/trend_quality/value_guard/quality/revenue_flow (see `analytics/score.py`), this
    tool is single-factor: raw price return only, the same "who moved the most over period X" a
    trader means by that phrase, not a multi-factor AI opinion. Reads the same already-ingested
    `PRICE_EOD_JOB`/`STOCK_INFO_JOB` Parquet partitions `GetAdvanceDeclineTool`/
    `GetSectorHeatmapTool` do — never a per-symbol live fan-out, same whole-market-aggregate
    reasoning as those tools' own docstrings.

    Public tier: both `Capability.PRICE_EOD` and `Capability.STOCK_INFO` are `DataClass.PUBLIC`
    (`sources/finmind/technical.py`), the same tier reasoning `GetAdvanceDeclineTool` gives."""

    name = "get_price_momentum"
    description = (
        "Get the whole-market top-N gainers/losers by pure close-to-close percentage change "
        "over a chosen lookback period (day/week/month, defaults to week, ending at the "
        "latest available day) — a single-factor price-momentum ranking, distinct from "
        "get_score_leaderboard's multi-factor composite score."
    )
    input_model: type[BaseModel] = GetPriceMomentumInput
    output_model: type[BaseModel] = GetPriceMomentumOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "price_momentum"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetPriceMomentumInput, args)
        market = typed.market.value
        calendar = _real_calendar(self._store, market)
        trade_date = typed.trade_date or _latest_dataset_date(
            self._store, PRICE_EOD_JOB.dataset, market
        )
        if trade_date is None or not self._store.has_partition(
            PRICE_EOD_JOB.dataset, market, trade_date
        ):
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        period_ago_date = calendar.shift_business_days(
            trade_date, -_MOMENTUM_PERIOD_TRADING_DAYS[typed.period.value]
        )
        if not self._store.has_partition(PRICE_EOD_JOB.dataset, market, period_ago_date):
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=trade_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window("price_eod", market, trade_date),
            )
        roster_date = _latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=trade_date,
                period_ago_trade_date=period_ago_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window("stock_info", market, trade_date),
            )

        today_closes = _closes_by_symbol(self._store, market, trade_date)
        period_ago_closes = _closes_by_symbol(self._store, market, period_ago_date)
        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = _rows_from_store(StockInfoRow, raw_roster)
        names_by_symbol = {row.symbol: row.name for row in roster}

        rows: list[PriceMomentumRow] = []
        for symbol, close in today_closes.items():
            period_ago_close = period_ago_closes.get(symbol)
            name = names_by_symbol.get(symbol)
            if not period_ago_close or not name:
                continue
            pct = (close - period_ago_close) / period_ago_close * 100
            rows.append(PriceMomentumRow(symbol=symbol, stock_name=name, pct_change=pct))

        ranked = sorted(rows, key=lambda r: r.pct_change, reverse=True)
        top_gainers = tuple(ranked[: typed.top_n])
        top_losers = tuple(sorted(ranked, key=lambda r: r.pct_change)[: typed.top_n])

        return GetPriceMomentumOutput(
            market=market,
            period=typed.period,
            trade_date=trade_date,
            period_ago_trade_date=period_ago_date,
            data_available=True,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )


# The tier scheme confirmed live against the real upstream — both TDCC's own web query form
# (https://www.tdcc.com.tw/portal/zh/smWeb/qryStock, symbol 2330, 2026-08-21 snapshot) and
# FinMind's Sponsor-tier `TaiwanStockHoldingSharesPer` return the IDENTICAL 17-row tier table
# with these exact upstream label strings — never a guessed/assumed boundary. Tier 16
# ("差異數調整") and 17 ("合計") are bookkeeping rows, not real holding-size tiers, and are
# deliberately excluded from every threshold sum below.
_HOLDER_TIER_LABELS: tuple[str, ...] = (
    "1-999",
    "1,000-5,000",
    "5,001-10,000",
    "10,001-15,000",
    "15,001-20,000",
    "20,001-30,000",
    "30,001-40,000",
    "40,001-50,000",
    "50,001-100,000",
    "100,001-200,000",
    "200,001-400,000",
    "400,001-600,000",
    "600,001-800,000",
    "800,001-1,000,000",
    "1,000,001以上",
)

# Threshold (張, 1張 = 1,000 shares) -> the tier labels whose shares sum to "holds at least this
# many張". Each threshold is every tier whose lower bound is >= that many shares — e.g. "400張"
# includes tier 12 (400,001-600,000) through the open-ended top tier, matching how the TDCC/
# goodinfo convention for "X張以上持股比例" is computed (a cumulative lower-bound sum, not one
# isolated bucket).
_HOLDER_THRESHOLDS: dict[int, tuple[str, ...]] = {
    200: _HOLDER_TIER_LABELS[10:],  # 200,001-400,000 and every tier above
    400: _HOLDER_TIER_LABELS[11:],  # 400,001-600,000 and every tier above
    800: _HOLDER_TIER_LABELS[13:],  # 800,001-1,000,000 and every tier above
    1000: _HOLDER_TIER_LABELS[14:],  # 1,000,001以上 only
}

_HOLDER_ACCUMULATION_TOP_N = 20


class HolderThreshold(IntEnum):
    TIER_200 = 200
    TIER_400 = 400
    TIER_800 = 800
    TIER_1000 = 1000


class HolderWindow(IntEnum):
    WEEK_1 = 1
    WEEK_2 = 2
    WEEK_3 = 3
    WEEK_4 = 4


class GetHolderAccumulationInput(BaseModel):
    """A market + which holding-size threshold to rank by + how many weekly snapshots back to
    compare against. `window` counts *snapshots*, not calendar weeks — this dataset publishes
    at most once per real trading week, so `window=2` means "2 snapshots ago", which is
    ordinarily ~2 calendar weeks but can stretch further across a holiday-shortened week with
    no snapshot at all (an honest gap, not silently treated as "no such week")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    threshold: HolderThreshold = Field(
        default=HolderThreshold.TIER_400,
        description="Holding-size threshold in 張 (1,000 shares): 200/400/800/1000, cumulative "
        "(e.g. 400 sums every tier holding >= 400,001 shares).",
    )
    window: HolderWindow = Field(
        default=HolderWindow.WEEK_2,
        description="How many weekly snapshots back to compare against: 1/2/3/4.",
    )
    as_of: date_ | None = Field(
        default=None,
        description="The snapshot date to rank as of (ISO date). Omitted or null means the "
        "latest ingested snapshot.",
    )
    top_n: int = Field(default=_HOLDER_ACCUMULATION_TOP_N, ge=1, le=50)


class HolderAccumulationRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    latest_close: float | None = None  # None if no PRICE_EOD_JOB bar exists for `as_of`
    percent_of_custody_before: float  # this threshold's summed 占集保庫存數比例% at `period_ago`
    percent_of_custody_after: float  # this threshold's summed 占集保庫存數比例% at `as_of`
    percent_of_custody_change: float  # after - before, in percentage points
    shares_change: int  # this threshold's summed shares, after - before


class GetHolderAccumulationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    threshold: HolderThreshold = HolderThreshold.TIER_400
    window: HolderWindow = HolderWindow.WEEK_2
    as_of: date_ | None = None
    period_ago: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    rows: tuple[HolderAccumulationRow, ...] = Field(default_factory=tuple)


class GetHolderAccumulationTool(BaseTool):
    """`get_holder_accumulation` — 大戶加碼股: which symbols grew a chosen large-holder tier's
    share-of-custody the most over 1-4 weekly snapshots, computed from the already-ingested
    `SHAREHOLDING_DISTRIBUTION_JOB` partitions (`ingest/jobs.py`) — never a per-symbol live
    fan-out across the whole ~2,700-symbol universe (see that job's own docstring for why a
    ranking read specifically needed a whole-market ingest path rather than reusing the
    Sponsor-tier per-symbol `get_shareholder_dispersion`/TDCC `get_tdcc_shareholding_distribution`
    tools' existing per-symbol shape).

    Tier scheme confirmed live against both TDCC's own web query tool and FinMind's
    `TaiwanStockHoldingSharesPer` (`_HOLDER_TIER_LABELS`'s own docstring) — never guessed.

    History caveat: `SHAREHOLDING_DISTRIBUTION_JOB` only started ingesting from whenever its
    daemon entry first ran; `window` snapshots further back than that simply are not available
    yet, disclosed via `unavailable_reason` rather than silently returning zero-width deltas.

    Sponsor tier: `Capability.SHAREHOLDING_DISTRIBUTION`'s underlying FinMind dataset
    (`TaiwanStockHoldingSharesPer`) is documented Backer/Sponsor-only (`sources/finmind/
    chip_institutional.py`'s own `_DatasetConfig.data_class`) — reading it back from the
    already-ingested derived store does not lift that gate, the same reasoning
    `GetIndexContributionTool` gives for its own Sponsor-tier ingested dataset."""

    name = "get_holder_accumulation"
    description = (
        "Get the whole-market ranking of which stocks grew a chosen large-holder tier "
        "(>=200/400/800/1,000張) share-of-custody the most over 1-4 weekly snapshots "
        "(Sponsor-tier; 集保戶股權分散表 week-over-week accumulation)."
    )
    input_model: type[BaseModel] = GetHolderAccumulationInput
    output_model: type[BaseModel] = GetHolderAccumulationOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "holder_accumulation"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def _tier_totals_by_symbol(
        self, market: str, snapshot_date: date_, threshold: HolderThreshold
    ) -> dict[str, tuple[float, int]] | None:
        """This threshold's summed `(percent_of_custody, shares)` per symbol at `snapshot_date`,
        or `None` if that snapshot has never been ingested."""
        if not self._store.has_partition(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market, snapshot_date
        ):
            return None
        raw_rows = self._store.read_partition(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market, snapshot_date
        )
        wanted_labels = set(_HOLDER_THRESHOLDS[threshold.value])
        totals: dict[str, tuple[float, int]] = {}
        for row in raw_rows:
            symbol = row.get("symbol")
            level = row.get("holding_shares_level")
            if not isinstance(symbol, str) or level not in wanted_labels:
                continue
            percent = row.get("percent")
            unit = row.get("unit")
            if not isinstance(percent, (int, float)) or not isinstance(unit, (int, float)):
                continue
            prior_percent, prior_shares = totals.get(symbol, (0.0, 0))
            totals[symbol] = (prior_percent + float(percent), prior_shares + int(unit))
        return totals

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetHolderAccumulationInput, args)
        market = typed.market.value

        snapshot_dates = self._store.list_partition_dates(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market
        )
        if not snapshot_dates:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "shareholding_distribution", market, typed.as_of
                ),
            )
        as_of = typed.as_of or snapshot_dates[-1]
        eligible = [d for d in snapshot_dates if d <= as_of]
        if not eligible or eligible[-1] != as_of:
            # `as_of` was explicitly requested but has no ingested snapshot — never silently
            # substitute a different date the caller did not ask for.
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=typed.as_of,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "shareholding_distribution", market, typed.as_of
                ),
            )
        idx = eligible.index(as_of)
        if idx < typed.window.value:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=as_of,
                data_available=False,
                unavailable_reason=(
                    f"Only {idx} weekly shareholding-distribution snapshot(s) ingested before "
                    f"{as_of.isoformat()} — need at least {typed.window.value} for a "
                    f"{typed.window.value}-week comparison. This history grows by one real "
                    "snapshot per week; no bulk backfill exists for this dataset."
                ),
            )
        period_ago = eligible[idx - typed.window.value]

        after = self._tier_totals_by_symbol(market, as_of, typed.threshold)
        before = self._tier_totals_by_symbol(market, period_ago, typed.threshold)
        if after is None or before is None:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=as_of,
                period_ago=period_ago,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "shareholding_distribution", market, as_of
                ),
            )

        roster_date = _latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        names_by_symbol: dict[str, str] = {}
        if roster_date is not None:
            raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
            roster_rows = _rows_from_store(StockInfoRow, raw_roster)
            names_by_symbol = {row.symbol: row.name for row in roster_rows}
        closes = _closes_by_symbol(self._store, market, as_of)

        rows: list[HolderAccumulationRow] = []
        for symbol, (percent_after, shares_after) in after.items():
            percent_before, shares_before = before.get(symbol, (0.0, 0))
            name = names_by_symbol.get(symbol)
            if not name:
                continue
            rows.append(
                HolderAccumulationRow(
                    symbol=symbol,
                    stock_name=name,
                    latest_close=closes.get(symbol),
                    percent_of_custody_before=percent_before,
                    percent_of_custody_after=percent_after,
                    percent_of_custody_change=percent_after - percent_before,
                    shares_change=shares_after - shares_before,
                )
            )
        ranked = sorted(rows, key=lambda r: r.percent_of_custody_change, reverse=True)
        return GetHolderAccumulationOutput(
            market=market,
            threshold=typed.threshold,
            window=typed.window,
            as_of=as_of,
            period_ago=period_ago,
            data_available=True,
            rows=tuple(ranked[: typed.top_n]),
        )


class GetIndexContributionInput(BaseModel):
    """A market + an optional trading day + a top-N cap on each side of the ranking — mirrors
    `GetAdvanceDeclineInput`'s date semantics and `GetEtfMoneyFlowInput.top_n`'s ranking cap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to estimate contribution for (ISO date). Omitted or null "
        "means the latest available trading day.",
    )
    top_n: int = Field(default=_INDEX_CONTRIBUTION_TOP_N, ge=1, le=100)


class IndexContributionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    weight_per: float  # 市值比重 (%) on trade_date, from MarketValueWeightRow
    pct_change: float  # (close - prior_close) / prior_close, a fraction not a percent
    contribution_estimate: float  # weight_per * pct_change — see contribution_caveat


class GetIndexContributionOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    contribution_caveat: str = Field(default=_CONTRIBUTION_CAVEAT, min_length=1)
    positive_contributors: tuple[IndexContributionRow, ...] = Field(default_factory=tuple)
    negative_contributors: tuple[IndexContributionRow, ...] = Field(default_factory=tuple)


class GetIndexContributionTool(BaseTool):
    """`get_index_contribution` — an APPROXIMATE per-symbol index-contribution estimate,
    `weight_per x pct_change` (close-to-close), joining the already-ingested
    `MARKET_VALUE_WEIGHT_JOB` weight partition against `PRICE_EOD_JOB` closes for the same
    two-day window `GetAdvanceDeclineTool` resolves (`_two_day_window`) — never a per-symbol
    live fan-out, for the same whole-market-aggregate reasoning as that tool.

    This is **not** TWSE's real index-contribution methodology, which uses a float-adjusted
    divisor this codebase has no data for (`MarketValueWeightRow`'s own `weight_per` is a raw
    market-cap-weight ranking, not a float-adjusted one) — `contribution_caveat` is therefore
    always populated (never empty, `Field(min_length=1)`) with that disclosure rather than
    only living in this docstring, mirroring how `currentness_caveat` is enforced elsewhere.

    Sponsor tier: `Capability.MARKET_VALUE_WEIGHT`'s underlying FinMind dataset
    (`TaiwanStockMarketValueWeight`) is documented Backer/Sponsor-only with no free per-symbol
    tier (`sources/finmind/fundamental.py`'s own `_DatasetConfig.data_class` for that
    capability). Reading it back from the already-ingested derived store does not lift that
    gate — the same reasoning `GetActiveEtfReportTool`/`GetEtfMoneyFlowTool` apply to their own
    Sponsor-tier ingested datasets (`ACTIVE_ETF_HOLDING`/`ACTIVE_ETF_HOLDING_CHANGE`): the
    entitlement tier tracks the underlying capability/dataset, not which code path reads it."""

    name = "get_index_contribution"
    description = (
        "Get an APPROXIMATE per-symbol estimate of contribution to a market-cap-weighted "
        "index's move for one trading day (market-cap weight x price change) — not TWSE's "
        "official float-adjusted contribution figure; see `contribution_caveat` (Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetIndexContributionInput
    output_model: type[BaseModel] = GetIndexContributionOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "index_contribution"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetIndexContributionInput, args)
        market = typed.market.value
        calendar = _real_calendar(self._store, market)
        window = _two_day_window(
            self._store, PRICE_EOD_JOB.dataset, market, calendar, typed.trade_date
        )
        if window is None:
            return GetIndexContributionOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        if not self._store.has_partition(MARKET_VALUE_WEIGHT_JOB.dataset, market, trade_date):
            return GetIndexContributionOutput(
                market=market,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "market_value_weight", market, trade_date
                ),
            )

        raw_weights = self._store.read_partition(
            MARKET_VALUE_WEIGHT_JOB.dataset, market, trade_date
        )
        weight_rows = _rows_from_store(MarketValueWeightRow, raw_weights)
        today_closes = _closes_by_symbol(self._store, market, trade_date)
        prior_closes = _closes_by_symbol(self._store, market, prior_date)

        rows: list[IndexContributionRow] = []
        for weight_row in weight_rows:
            close = today_closes.get(weight_row.symbol)
            prior_close = prior_closes.get(weight_row.symbol)
            if close is None or not prior_close:  # missing/zero prior close — never divide by it
                continue
            pct_change = (close - prior_close) / prior_close
            rows.append(
                IndexContributionRow(
                    symbol=weight_row.symbol,
                    stock_name=weight_row.stock_name,
                    weight_per=weight_row.weight_per,
                    pct_change=pct_change,
                    contribution_estimate=weight_row.weight_per * pct_change,
                )
            )

        ranked = sorted(rows, key=lambda r: abs(r.contribution_estimate), reverse=True)
        positive = tuple(r for r in ranked if r.contribution_estimate > 0)[: typed.top_n]
        negative = tuple(r for r in ranked if r.contribution_estimate < 0)[: typed.top_n]

        return GetIndexContributionOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            positive_contributors=positive,
            negative_contributors=negative,
        )


def _turnovers_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every real equity/ETF symbol's trading-value (`turnover`, NT$) on `trade_date` from the
    same `PRICE_EOD_JOB` partition `_closes_by_symbol` reads (same warrant-exclusion reasoning —
    see that function's docstring) — `get_sector_heatmap`'s optional trading-value weighting
    input. `EODBar.turnover` is `None` on some rows (TWSE OpenAPI's own single-latest-snapshot
    shape never carries it, only FinMind's whole-market pull does) — a row with no turnover is
    simply absent from this map, never coerced to a fabricated `0.0` that would silently
    zero-weight it in the weighted average."""
    raw_bars = store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
    turnovers: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        turnover = row.get("turnover")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol)
            and isinstance(turnover, (int, float))
        ):
            turnovers[symbol] = float(turnover)
    return turnovers


class SectorHeatmapRow(BaseModel):
    """One industry's aggregate tile — colored by `avg_pct_change`, sized by `stock_count` (or
    `total_turnover`, when trading-value weighting was available for this industry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    industry: str
    avg_pct_change: float  # a whole-number percent (already `x100`), matching AdvanceDeclineBuckets
    stock_count: int
    advancing_count: int
    declining_count: int
    total_turnover: float | None = None  # sum of this industry's trading value on trade_date, when
    # every one of its members' `turnover` was available; `None` (never partially-summed) if any
    # member's `turnover` was missing, so a caller never mistakes a partial sum for the industry's
    # real total.
    weighted_by_turnover: bool  # whether avg_pct_change was trading-value-weighted (True) or a
    # simple per-stock average (False) — see class docstring for when each applies.


class GetSectorHeatmapInput(BaseModel):
    """A market + an optional trading day; omitted/`None` means "latest available" — same
    semantics as `GetAdvanceDeclineInput`/`GetIndexContributionInput`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report the sector heatmap for (ISO date). Omitted or null "
        "means the latest available trading day.",
    )


class GetSectorHeatmapOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    industries: tuple[SectorHeatmapRow, ...] = Field(default_factory=tuple)


def _sector_heatmap_rows(
    *,
    industry_by_symbol: dict[str, str],
    today_closes: dict[str, float],
    prior_closes: dict[str, float],
    turnovers: dict[str, float],
) -> list[SectorHeatmapRow]:
    """Join the roster's `industry_category` against each symbol's close-to-close pct change,
    group by industry, and aggregate — trading-value-weighted when every member of that industry
    has a `turnover` figure for `trade_date`, else a simple per-stock average (never a partial
    weighted average silently mixing weighted and unweighted members)."""
    by_industry: dict[str, list[tuple[str, float]]] = {}
    for symbol, industry in industry_by_symbol.items():
        close = today_closes.get(symbol)
        prior_close = prior_closes.get(symbol)
        if close is None or not prior_close:
            # missing/legitimately-zero prior close — skip this symbol, never divide by it.
            continue
        pct = (close - prior_close) / prior_close * 100
        by_industry.setdefault(industry, []).append((symbol, pct))

    rows: list[SectorHeatmapRow] = []
    for industry, members in by_industry.items():
        pct_changes = [pct for _, pct in members]
        advancing = sum(1 for pct in pct_changes if pct > 0)
        declining = sum(1 for pct in pct_changes if pct < 0)
        member_turnovers = [turnovers.get(symbol) for symbol, _ in members]
        weighted = (
            all(t is not None for t in member_turnovers)
            and sum(cast("list[float]", member_turnovers)) > 0
        )
        if weighted:
            typed_turnovers = cast("list[float]", member_turnovers)
            total_turnover = sum(typed_turnovers)
            avg_pct_change = (
                sum(pct * t for (_, pct), t in zip(members, typed_turnovers, strict=True))
                / total_turnover
            )
        else:
            total_turnover = None
            avg_pct_change = sum(pct_changes) / len(pct_changes)
        rows.append(
            SectorHeatmapRow(
                industry=industry,
                avg_pct_change=avg_pct_change,
                stock_count=len(members),
                advancing_count=advancing,
                declining_count=declining,
                total_turnover=total_turnover,
                weighted_by_turnover=weighted,
            )
        )
    return rows


class GetSectorHeatmapTool(BaseTool):
    """`get_sector_heatmap` — 產業別漲跌熱力圖: joins the already-ingested `STOCK_INFO_JOB` roster
    (each symbol's `industry_category`) against the same two-day `PRICE_EOD_JOB` close window
    `GetAdvanceDeclineTool`/`GetIndexContributionTool` resolve (`_two_day_window`), grouping each
    symbol's close-to-close percentage change by industry — never a per-symbol live fan-out, for
    the same whole-market-aggregate reasoning those two tools document. `avg_pct_change` is
    trading-value-weighted (via `PRICE_EOD_JOB`'s own `turnover` field) when every member of an
    industry has one for `trade_date`, else a simple per-stock average — see `SectorHeatmapRow`'s
    `weighted_by_turnover` flag, never silently mixed.

    Public tier: both `Capability.STOCK_INFO` and `Capability.PRICE_EOD` are `DataClass.PUBLIC`
    in their own `_DatasetConfig` (`sources/finmind/technical.py`) — the same tier reasoning
    `GetAdvanceDeclineTool`'s own docstring gives for reading a PUBLIC-tier ingested dataset back
    from the derived store."""

    name = "get_sector_heatmap"
    description = (
        "Get the whole-market industry/sector heatmap for one trading day (defaults to the "
        "latest available day): each industry's average close-to-close percentage change "
        "(trading-value-weighted when available) and constituent stock count."
    )
    input_model: type[BaseModel] = GetSectorHeatmapInput
    output_model: type[BaseModel] = GetSectorHeatmapOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_heatmap"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetSectorHeatmapInput, args)
        market = typed.market.value
        calendar = _real_calendar(self._store, market)
        window = _two_day_window(
            self._store, PRICE_EOD_JOB.dataset, market, calendar, typed.trade_date
        )
        if window is None:
            return GetSectorHeatmapOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        roster_date = _latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetSectorHeatmapOutput(
                market=market,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window("stock_info", market, trade_date),
            )

        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = _rows_from_store(StockInfoRow, raw_roster)
        industry_by_symbol = {
            row.symbol: row.industry_category for row in roster if row.industry_category
        }
        today_closes = _closes_by_symbol(self._store, market, trade_date)
        prior_closes = _closes_by_symbol(self._store, market, prior_date)
        turnovers = _turnovers_by_symbol(self._store, market, trade_date)

        rows = _sector_heatmap_rows(
            industry_by_symbol=industry_by_symbol,
            today_closes=today_closes,
            prior_closes=prior_closes,
            turnovers=turnovers,
        )
        # Every industry present, ranked by move (not top-N truncated) — a heatmap tile list is
        # meant to show the whole market's industry breadth, unlike `get_index_contribution`'s
        # deliberately-capped ranking of individual symbols.
        ranked = tuple(sorted(rows, key=lambda r: r.avg_pct_change, reverse=True))

        return GetSectorHeatmapOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            industries=ranked,
        )


def _latest_eps(payload: dict[str, object]) -> float | None:
    """`financials`' payload is `{"lines": [...]}` — one row per (period, statement line);
    the most recent `item == "EPS"` row's `value` is the latest reported EPS."""
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        return None
    rows = cast(_RowList, raw_lines)
    eps_rows = [row for row in rows if row.get("item") == "EPS"]
    if not eps_rows:
        return None
    latest = max(eps_rows, key=lambda row: cast("str", row.get("period_end", "")))
    value = latest.get("value")
    return float(cast("float", value)) if isinstance(value, int | float) else None


# MOPS-sourced identity fields (`Capability.PROFILE`'s `profile` payload) that map straight into
# `GetCompanyProfileOutput` — named once here so the enrichment loop and `missing_fields` fallback
# both walk the exact same set instead of two hand-kept lists drifting apart.
_MOPS_PROFILE_FIELDS: tuple[str, ...] = (
    "chairman",
    "auditor",
    "headquarters",
    "website",
    "capital",
    "spokesperson",
    "founding_date",
    "industry_classification_code",
)

# `revenue_yoy`/`margins` are read back from the derived store (whole-market-ingested
# `TaiwanStockFinancialStatements`/`TaiwanStockMonthRevenue`, same pattern `get_etf_money_flow`
# uses for its own cross-symbol read) rather than fetched live through the router — see
# `analytics/fundamentals.py`'s module docstring. Lookback windows are trading days ending today:
# 400 comfortably spans several quarterly filing cycles (mirrors `scoring_batch.py`'s own
# `_FINANCIALS_LOOKBACK_DAYS`); revenue filings land monthly, so 400 trading days (~19 months)
# reliably covers the same calendar month one year prior even with a late filing.
_FINANCIALS_STORE_LOOKBACK_DAYS = 400
_MONTH_REVENUE_STORE_LOOKBACK_DAYS = 400


class GetCompanyProfileOutput(BaseModel):
    """A company-profile card composed from every capability wired below: `sector_class`
    (industry/sector), `dividends` (latest announced dividend), `market_value`/
    `valuation_ratios` (市值/PER/殖利率), `financials` (EPS, from the income-statement lines),
    `profile` (MOPS-sourced identity fields — 董事長/簽證會計師/住址/官網/資本額/發言人/成立日期/
    產業別), and two derived-store reads: `revenue_yoy` (this month's filed revenue vs the same
    calendar month a year prior, from the ingested `TaiwanStockMonthRevenue` dataset) and
    `margins` (TTM gross profit ÷ TTM revenue, from the same ingested statement lines `eps`
    reads — see `analytics/fundamentals.py`). Each field is only ever populated from a genuinely
    healthy fetch/read for this call; `missing_fields` is computed per-call from which fetches
    actually succeeded, never a static reduced list, so the agent never claims completeness it
    doesn't have."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    sector: str | None = None
    industry: str | None = None
    latest_dividend: DividendEvent | None = None
    market_value: int | None = None
    per: float | None = None
    dividend_yield: float | None = None
    eps: float | None = None
    chairman: str | None = None
    auditor: str | None = None
    headquarters: str | None = None
    website: str | None = None
    capital: int | None = None
    spokesperson: str | None = None
    founding_date: str | None = None
    industry_classification_code: str | None = None
    revenue_yoy: float | None = None
    margins: float | None = None
    sector_caveat: str = Field(min_length=1)
    dividend_caveat: str = Field(min_length=1)
    missing_fields: tuple[str, ...] = Field(
        default=(),
        description="Fields this profile call could not populate — either no adapter exists "
        "for that capability, or this call's fetch of it was unhealthy/empty. Never silently "
        "omitted from the caller.",
    )


class GetCompanyProfileTool(BaseTool):
    """`get_company_profile` — composes `sector_class` + `dividends` + `market_value` +
    `valuation_ratios` + `financials` + `profile` (MOPS) into one card, plus `revenue_yoy`/
    `margins` read back from the derived Parquet store (see `analytics/fundamentals.py`) rather
    than fetched live — same "whole-market dataset, read back not fetched live" pattern
    `get_etf_money_flow` uses. Every field is real when its capability's fetch/read is healthy for
    this call; otherwise it's `None` and named in `missing_fields` rather than faked. Immediate
    core."""

    name = "get_company_profile"
    description = (
        "Get a company-profile card for one stock: sector/industry classification, latest "
        "announced dividend, market value/PER/dividend yield/EPS, revenue YoY, gross margin, and "
        "MOPS-sourced identity fields (chairman/auditor/headquarters/website/capital/"
        "spokesperson/founding date/industry classification code). See `missing_fields` for "
        "anything this call couldn't populate."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetCompanyProfileOutput
    card_type: str | None = "company_profile"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter, store: ParquetStore) -> None:
        self._router = router
        self._store = store

    @staticmethod
    def _sector_and_industry(sector_payload: dict[str, object]) -> tuple[str | None, str | None]:
        classification = sector_payload.get("classification")
        if not isinstance(classification, dict):
            return None, None
        parsed = SectorClassification.model_validate(cast(_UntypedDict, classification))
        return parsed.sector, parsed.industry

    @staticmethod
    def _latest_dividend(dividend_payload: dict[str, object]) -> DividendEvent | None:
        raw_events = dividend_payload.get("events")
        if not (isinstance(raw_events, list) and raw_events):
            return None
        rows = cast(_RowList, raw_events)
        return DividendEvent.model_validate(rows[-1])

    @staticmethod
    def _mops_fields(profile_payload: dict[str, object]) -> dict[str, object | None]:
        mops_fields: dict[str, object | None] = dict.fromkeys(_MOPS_PROFILE_FIELDS)
        mops_profile = profile_payload.get("profile")
        if isinstance(mops_profile, dict):
            typed_profile = cast(_UntypedDict, mops_profile)
            for field_name in _MOPS_PROFILE_FIELDS:
                mops_fields[field_name] = typed_profile.get(field_name)
        return mops_fields

    def _revenue_yoy_and_margins(
        self, *, symbol: Symbol, market: Market
    ) -> tuple[float | None, float | None]:
        """Reads back the two derived-store-backed fields. `financials_lines` (income/balance/
        cash-flow) already backs `margins` via the same TTM gross-profit/revenue lines
        `compute_fundamental_metrics` uses; `month_revenue_rows` backs `revenue_yoy` via
        `compute_revenue_yoy`. Both degrade to `None` independently when their own store read has
        no rows for this symbol yet (not-yet-ingested/no-filing), never a fabricated number."""
        calendar = _real_calendar(self._store, market.value)
        today = date_.today()
        financials_window = calendar.business_days_between(
            calendar.shift_business_days(today, -(_FINANCIALS_STORE_LOOKBACK_DAYS - 1)), today
        )
        revenue_window = calendar.business_days_between(
            calendar.shift_business_days(today, -(_MONTH_REVENUE_STORE_LOOKBACK_DAYS - 1)), today
        )
        lines = load_symbol_financials(
            self._store, market=market, run_dates=financials_window, symbol=symbol.id
        )
        revenue_rows = load_symbol_month_revenue(
            self._store, market=market, run_dates=revenue_window, symbol=symbol.id
        )
        market_value = load_symbol_market_value(
            self._store, market=market, run_dates=financials_window, symbol=symbol.id
        )
        margins = compute_fundamental_metrics(lines, market_value).gross_margin
        revenue_yoy = compute_revenue_yoy(revenue_rows)
        return revenue_yoy, margins

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        # Six independent per-capability fetches for the same symbol — none of their requests or
        # post-processing depends on another's result, so they run concurrently rather than one
        # await at a time. Sequential awaits here previously meant this single tool call could
        # take 40+ seconds with zero intermediate `tool.start`/`tool.result` events (each fetch is
        # inside this one dispatch, not its own agent-loop step) — a live process timeline showed
        # a single unchanging "查詢中" status the whole time, indistinguishable from a hang.
        # `router.fetch` never raises for a genuine no-data case (degrades to an `unavailable`
        # `SourceResult`), so no `return_exceptions` handling is needed beyond what sequential
        # awaits already had.
        (
            sector_result,
            dividend_result,
            market_value_result,
            valuation_result,
            financials_result,
            profile_result,
        ) = await asyncio.gather(
            self._router.fetch(Capability.SECTOR_CLASS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.DIVIDENDS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.MARKET_VALUE, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.VALUATION_RATIOS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.FINANCIALS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.PROFILE, symbol, tls_policy=ctx.tls_policy),
        )

        missing_fields: list[str] = []

        sector, industry = self._sector_and_industry(sector_result.payload)
        latest_dividend = self._latest_dividend(dividend_result.payload)

        market_value_rows = validate_rows(MarketValueRow, market_value_result.payload, "rows")
        market_value = market_value_rows[-1].market_value if market_value_rows else None
        if market_value is None:
            missing_fields.append("market_value")

        valuation_rows = validate_rows(ValuationRatiosRow, valuation_result.payload, "rows")
        latest_valuation = valuation_rows[-1] if valuation_rows else None
        per = latest_valuation.per if latest_valuation else None
        dividend_yield = latest_valuation.dividend_yield if latest_valuation else None
        if per is None:
            missing_fields.append("per")
        if dividend_yield is None:
            missing_fields.append("dividend_yield")

        eps = _latest_eps(financials_result.payload)
        if eps is None:
            missing_fields.append("eps")

        revenue_yoy, margins = self._revenue_yoy_and_margins(symbol=symbol, market=typed.market)
        if revenue_yoy is None:
            missing_fields.append("revenue_yoy")
        if margins is None:
            missing_fields.append("margins")

        mops_fields = self._mops_fields(profile_result.payload)
        missing_fields.extend(
            field_name for field_name in _MOPS_PROFILE_FIELDS if mops_fields[field_name] is None
        )

        return GetCompanyProfileOutput(
            symbol=symbol.id,
            market=symbol.market.value,
            sector=sector,
            industry=industry,
            latest_dividend=latest_dividend,
            market_value=market_value,
            per=per,
            dividend_yield=dividend_yield,
            eps=eps,
            chairman=cast(_OptionalStr, mops_fields["chairman"]),
            auditor=cast(_OptionalStr, mops_fields["auditor"]),
            headquarters=cast(_OptionalStr, mops_fields["headquarters"]),
            website=cast(_OptionalStr, mops_fields["website"]),
            capital=cast("int | None", mops_fields["capital"]),
            spokesperson=cast(_OptionalStr, mops_fields["spokesperson"]),
            founding_date=cast(_OptionalStr, mops_fields["founding_date"]),
            industry_classification_code=cast(
                "str | None", mops_fields["industry_classification_code"]
            ),
            revenue_yoy=revenue_yoy,
            margins=margins,
            sector_caveat=sector_result.currentness_caveat,
            dividend_caveat=dividend_result.currentness_caveat,
            missing_fields=tuple(missing_fields),
        )


_MAX_BATCH_SYMBOLS = 20  # mirrors agent.guardrails._MAX_SYMBOLS_PER_CALL — one bounded call, never
# a full-market scan; also matches the AI-leaderboard's own max row count (10 default/20 expanded)
_MAX_PRICE_HISTORY_DAYS = 60  # bounded lookback window for a sparkline — never an open-ended range


class GetSymbolInfoInput(BaseModel):
    """A bounded symbol list (≤20) + market — never an "all symbols" shape; the same bound
    `agent.guardrails.check_tool_call` enforces on every list-valued tool argument."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: tuple[str, ...] = Field(min_length=1, max_length=_MAX_BATCH_SYMBOLS)
    market: Market = Market.TW


class GetSymbolInfoOutput(BaseModel):
    """One row per symbol that exists in the latest ingested roster — a symbol not found there
    (delisted, mistyped, or not yet in an ETF/warrant roster this dataset covers) is simply
    absent from `rows`, never a fabricated placeholder name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    roster_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    rows: tuple[StockInfoRow, ...] = Field(default_factory=tuple)


class GetSymbolInfoTool(BaseTool):
    """`get_symbol_info` — batched symbol->name (+ industry/security-type) lookup against the
    whole-market `STOCK_INFO_JOB` roster the ingest layer already writes daily. This is the one
    real gap the AI leaderboard (and any other bare-symbol list) has today: `ScoreResult`/
    `ActiveEtfInfoRow`/etc. all carry a `symbol` but no name, and until now nothing surfaced
    `StockInfoRow.name` outside `get_sector_heatmap`'s internal join. Reads the ingest-owned
    Parquet store directly (no router fetch) — the exact same roster-read pattern
    `GetSectorHeatmapTool` already uses, just exposed as its own tool instead of staying
    internal to one caller."""

    name = "get_symbol_info"
    description = (
        "Get the company/fund name, industry category, and security type for up to 20 "
        "stock/ETF symbols in one bounded call, from the whole-market reference roster."
    )
    input_model: type[BaseModel] = GetSymbolInfoInput
    output_model: type[BaseModel] = GetSymbolInfoOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "symbol_info"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetSymbolInfoInput, args)
        market = typed.market.value
        roster_date = _latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetSymbolInfoOutput(
                market=market,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window("stock_info", market, None),
            )
        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = _rows_from_store(StockInfoRow, raw_roster)
        wanted = set(typed.symbols)
        rows = tuple(row for row in roster if row.symbol in wanted)
        return GetSymbolInfoOutput(market=market, roster_date=roster_date, rows=rows)


class GetPriceHistoryInput(BaseModel):
    """A bounded symbol list (≤20) + market + a bounded trailing-day count — the sparkline/chg%
    read. `days` counts trading days, not calendar days, and is capped well short of a "give me
    everything" range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: tuple[str, ...] = Field(min_length=1, max_length=_MAX_BATCH_SYMBOLS)
    market: Market = Market.TW
    days: int = Field(default=20, ge=2, le=_MAX_PRICE_HISTORY_DAYS)


class GetPriceHistoryOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    start_date: date_ | None = None
    end_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    # One EODBar per (symbol, trading day) actually ingested — a caller derives its own sparkline
    # (the close series) and %-change (latest vs. first/previous close) from this; this tool
    # never computes or asserts a percentage itself, so there is exactly one place that math
    # lives, not one copy per caller.
    rows: tuple[EODBar, ...] = Field(default_factory=tuple)


class GetPriceHistoryTool(BaseTool):
    """`get_price_history` — a bounded multi-day OHLCV window for up to 20 symbols, read from the
    already-ingested `PRICE_EOD_JOB` partitions (never a per-symbol live fetch). Backs a
    sparkline and a client-computed chg% for any symbol list (leaderboard rows, a watchlist, an
    ETF roster) — `get_price_quote` deliberately stays latest-bar-only (see its own docstring);
    this is the separate bounded-history capability those callers need instead of widening that
    narrow tool's contract."""

    name = "get_price_history"
    description = (
        "Get a bounded trailing window (2-60 trading days) of daily OHLCV bars for up to 20 "
        "stock/ETF symbols in one call — the data behind a sparkline or a %-change figure."
    )
    input_model: type[BaseModel] = GetPriceHistoryInput
    output_model: type[BaseModel] = GetPriceHistoryOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "price_history"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetPriceHistoryInput, args)
        market = typed.market.value
        latest = _latest_dataset_date(self._store, PRICE_EOD_JOB.dataset, market)
        if latest is None:
            return GetPriceHistoryOutput(
                market=market,
                data_available=False,
                unavailable_reason=_unavailable_reason_for_window("price_eod", market, None),
            )
        calendar = _real_calendar(self._store, market)
        start = calendar.shift_business_days(latest, -(typed.days - 1))
        dates = calendar.business_days_between(start, latest)
        raw_rows = self._store.read_range(PRICE_EOD_JOB.dataset, market, dates)
        wanted = set(typed.symbols)
        rows = tuple(
            row
            for row in _rows_from_store(EODBar, raw_rows)
            if row.symbol in wanted and row.market.value == market
        )
        return GetPriceHistoryOutput(market=market, start_date=start, end_date=latest, rows=rows)


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table.

    `get_company_profile` is a named immediate-core tool, built narrow here (see its
    class docstring). `get_price_quote` backs the basic price-quote read that's also part of the
    immediate-core set. Everything else here is a real, deferred capability.

    `get_etf_money_flow` (and, for its `revenue_yoy`/`margins` fields only, `get_company_profile`)
    read the ingest-owned Parquet store directly (see their class docstrings for why) rather than
    the router, so this function builds its own `ParquetStore` from `config.settings` here rather
    than the router this function otherwise only threads through. `get_advance_decline`,
    `get_index_contribution`, and `get_sector_heatmap` are the same store-reading family — see
    their own class docstrings.
    """
    store = ParquetStore(root=get_settings().parquet_root)
    tools: list[tuple[Tool, bool]] = [
        (GetCompanyProfileTool(router, store), True),
        (GetPriceQuoteTool(router), True),
        (GetDividendScheduleTool(router), False),
        (GetInstitutionalFlowTool(router), False),
        (GetChipFlowsTool(router), False),
        (GetActiveEtfReportTool(router, store), False),
        (GetEtfMoneyFlowTool(store), False),
        (GetAdvanceDeclineTool(store), False),
        (GetPriceMomentumTool(store), False),
        (GetHolderAccumulationTool(store), False),
        (GetIndexContributionTool(store), False),
        (GetSectorHeatmapTool(store), False),
        (GetSymbolInfoTool(store), False),
        (GetPriceHistoryTool(store), False),
    ]
    return tools


__all__ = [
    "ActiveEtfReportRow",
    "AdvanceDeclineBuckets",
    "EtfMoneyFlowRow",
    "GetActiveEtfReportInput",
    "GetActiveEtfReportOutput",
    "GetActiveEtfReportTool",
    "GetAdvanceDeclineInput",
    "GetAdvanceDeclineOutput",
    "GetAdvanceDeclineTool",
    "GetChipFlowsOutput",
    "GetChipFlowsTool",
    "GetCompanyProfileOutput",
    "GetCompanyProfileTool",
    "GetDividendScheduleOutput",
    "GetDividendScheduleTool",
    "GetEtfMoneyFlowInput",
    "GetEtfMoneyFlowOutput",
    "GetEtfMoneyFlowTool",
    "GetHolderAccumulationInput",
    "GetHolderAccumulationOutput",
    "GetHolderAccumulationTool",
    "GetIndexContributionInput",
    "GetIndexContributionOutput",
    "GetIndexContributionTool",
    "GetInstitutionalFlowOutput",
    "GetInstitutionalFlowTool",
    "GetPriceHistoryInput",
    "GetPriceHistoryOutput",
    "GetPriceHistoryTool",
    "GetPriceMomentumInput",
    "GetPriceMomentumOutput",
    "GetPriceMomentumTool",
    "GetPriceQuoteOutput",
    "GetPriceQuoteTool",
    "GetSectorHeatmapInput",
    "GetSectorHeatmapOutput",
    "GetSectorHeatmapTool",
    "GetSymbolInfoInput",
    "GetSymbolInfoOutput",
    "GetSymbolInfoTool",
    "HolderAccumulationRow",
    "HolderThreshold",
    "HolderWindow",
    "HoldingStatus",
    "IndexContributionRow",
    "MomentumPeriod",
    "PriceMomentumRow",
    "SectorHeatmapRow",
    "build_registration_table",
]
