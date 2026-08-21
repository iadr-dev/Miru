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
from calendar.trading_days import TradingDayCalendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.fundamentals import (
    compute_fundamental_metrics,
    compute_revenue_yoy,
    load_symbol_financials,
    load_symbol_market_value,
    load_symbol_month_revenue,
)
from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import ACTIVE_ETF_HOLDING_CHANGE_JOB, ACTIVE_ETF_HOLDING_JOB, PRICE_EOD_JOB
from ingest.store import ParquetStore
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    ChipBrokerRow,
    DividendEvent,
    EODBar,
    InstitutionalFlowRow,
    MarketValueRow,
    SectorClassification,
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
    premium_discount_pct: float | None = None  # 折溢價 — always None, see unavailable_reason
    premium_discount_unavailable_reason: str | None = _PREMIUM_DISCOUNT_UNAVAILABLE_REASON
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


def _closes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every symbol's closing price on `trade_date`, read from the whole-market
    `TaiwanStockPrice` partition the daily ingest job already wrote — the same derived-store
    read `GetEtfMoneyFlowTool` uses, not a per-symbol live fetch (`_active_etf_holding_value`'s
    own docstring explains why this beats trusting FinMind's own `market_value` field)."""
    raw_bars = store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
    closes: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        close = row.get("close")
        if isinstance(symbol, str) and isinstance(close, (int, float)):
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
            rows=tuple(rows),
        )


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
        calendar = TradingDayCalendar()
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
        calendar = TradingDayCalendar()
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


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table.

    `get_company_profile` is a named immediate-core tool, built narrow here (see its
    class docstring). `get_price_quote` backs the basic price-quote read that's also part of the
    immediate-core set. Everything else here is a real, deferred capability.

    `get_etf_money_flow` (and, for its `revenue_yoy`/`margins` fields only, `get_company_profile`)
    read the ingest-owned Parquet store directly (see their class docstrings for why) rather than
    the router, so this function builds its own `ParquetStore` from `config.settings` here rather
    than the router this function otherwise only threads through.
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
    ]
    return tools


__all__ = [
    "ActiveEtfReportRow",
    "EtfMoneyFlowRow",
    "GetActiveEtfReportInput",
    "GetActiveEtfReportOutput",
    "GetActiveEtfReportTool",
    "GetChipFlowsOutput",
    "GetChipFlowsTool",
    "GetCompanyProfileOutput",
    "GetCompanyProfileTool",
    "GetDividendScheduleOutput",
    "GetDividendScheduleTool",
    "GetEtfMoneyFlowInput",
    "GetEtfMoneyFlowOutput",
    "GetEtfMoneyFlowTool",
    "GetInstitutionalFlowOutput",
    "GetInstitutionalFlowTool",
    "GetPriceQuoteOutput",
    "GetPriceQuoteTool",
    "HoldingStatus",
    "build_registration_table",
]
