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

from calendar.trading_days import TradingDayCalendar
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import ACTIVE_ETF_HOLDING_CHANGE_JOB, ACTIVE_ETF_HOLDING_JOB
from ingest.store import ParquetStore
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    ChipBrokerRow,
    DividendEvent,
    EODBar,
    InstitutionalFlowRow,
    SectorClassification,
)
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput, SymbolInput

# The untyped row-list edge every `cast(...)` below narrows a `SourceResult.payload` value from
# — one PEP-695 local alias instead of repeating the same cast-target string at every call site.
type _RowList = list[dict[str, object]]


def _latest_bar(payload: dict[str, object]) -> EODBar | None:
    """`price_eod` payload is `{"bars": [...]}` (FinMind, possibly many rows) or `{"bar": {...}}`
    (TWSE OpenAPI, single latest snapshot) — normalize both shapes to "the latest bar"."""
    bars = payload.get("bars")
    if isinstance(bars, list) and bars:
        rows = cast(_RowList, bars)
        return EODBar.model_validate(rows[-1])
    bar = payload.get("bar")
    if isinstance(bar, dict):
        return EODBar.model_validate(cast("dict[str, object]", bar))
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
    data_class = DataClass.PUBLIC
    card_type: str | None = "price_quote"

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
    data_class = DataClass.PUBLIC
    card_type: str | None = "dividend_schedule"

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


class GetInstitutionalFlowOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    rows: tuple[InstitutionalFlowRow, ...] = Field(default_factory=tuple)


class GetInstitutionalFlowTool(BaseTool):
    """`get_institutional_flow` — wraps `Capability.INSTITUTIONAL_FLOW` (三大法人買賣). Public,
    deferred."""

    name = "get_institutional_flow"
    description = (
        "Get the three-major-institutional-investor (foreign/investment-trust/dealer) "
        "buy/sell rows for one stock/ETF symbol over an optional date range."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetInstitutionalFlowOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "institutional_flow"

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
        )


class GetChipFlowsOutput(BaseModel):
    """Sponsor-tier 分點 branch trading rows. Returned as-is by `run()` — `dispatch_tool` (not
    this class) is what substitutes the `MaskedTeaser` for an unentitled caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    rows: tuple[ChipBrokerRow, ...] = Field(default_factory=tuple)


class GetChipFlowsTool(BaseTool):
    """`get_chip_flows` — wraps `Capability.CHIP_BROKER` (FinMind Sponsor-tier 分點進出).

    The sponsor-tier worked example: this tool's own `run()` has no entitlement logic at all —
    it always fetches and returns real data. Gating happens exactly once, in
    `tools.base.dispatch_tool`, which checks `data_class` before ever calling `run()`.
    """

    name = "get_chip_flows"
    description = (
        "Get branch(分點)/broker-level buy-sell trading rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetChipFlowsOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "chip_flows"

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


def _active_etf_fund_size_fields(
    *,
    report_holdings: dict[str, ActiveEtfHoldingRow],
    prior_holdings: dict[str, ActiveEtfHoldingRow],
) -> tuple[float | None, float | None, float | None]:
    """`(fund_size, fund_size_prior, fund_size_change_pct)` — pulled out of
    `GetActiveEtfReportTool.run` for the same shallow-branching reason as the row-builders."""
    fund_size = sum(h.market_value for h in report_holdings.values()) if report_holdings else None
    fund_size_prior = (
        sum(h.market_value for h in prior_holdings.values()) if prior_holdings else None
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
    snapshot, for weight/market-value) + `Capability.ACTIVE_ETF_HOLDING_CHANGE` (FinMind's own
    pre-diffed buy/sell) via the router — a single-ETF report is always a per-symbol read, never
    the whole-market ingest path."""

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

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

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
        raw_holdings = holding_result.payload.get("holdings")
        holdings: tuple[ActiveEtfHoldingRow, ...] = ()
        if isinstance(raw_holdings, list):
            typed_holdings = cast(_RowList, raw_holdings)
            holdings = tuple(ActiveEtfHoldingRow.model_validate(row) for row in typed_holdings)

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
        raw_changes = change_result.payload.get("changes")
        changes: tuple[ActiveEtfHoldingChangeRow, ...] = ()
        if isinstance(raw_changes, list):
            typed_changes = cast(_RowList, raw_changes)
            changes = tuple(ActiveEtfHoldingChangeRow.model_validate(row) for row in typed_changes)

        rows = _active_etf_report_rows(
            changes,
            prior_holdings=prior_holdings,
            report_holdings=report_holdings,
            has_prior_day=prior_date is not None,
        )
        fund_size, fund_size_prior, fund_size_change_pct = _active_etf_fund_size_fields(
            report_holdings=report_holdings, prior_holdings=prior_holdings
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


class GetCompanyProfileOutput(BaseModel):
    """A **partial** company-profile card: only the fields two real capabilities
    (`sector_class`, `dividends`) can back today. The design calls for this tool to be a MOPS +
    FinMind composite (產業分類/成立年份/董事長/總部/官網/資本額/發言人/簽證會計師 + financial
    snapshot) — no MOPS adapter exists (Phase 2 didn't build one), so every MOPS-sourced field
    is omitted rather than faked; `missing_fields` names exactly what's absent so the agent
    never claims completeness it doesn't have."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    sector: str | None = None
    industry: str | None = None
    latest_dividend: DividendEvent | None = None
    sector_caveat: str = Field(min_length=1)
    dividend_caveat: str = Field(min_length=1)
    missing_fields: tuple[str, ...] = Field(
        default=(
            "industry_classification_code",
            "founding_year",
            "chairman",
            "headquarters",
            "website",
            "capital",
            "spokesperson",
            "auditor",
            "market_value",
            "per",
            "revenue_yoy",
            "margins",
            "eps",
            "dividend_yield",
        ),
        description="MOPS-sourced / derived-analytics fields this profile cannot populate "
        "today (no MOPS adapter, no analytics layer) — never silently omitted from the caller.",
    )


class GetCompanyProfileTool(BaseTool):
    """`get_company_profile` — narrowed to what `sector_class` (yfinance) + `dividends`
    (FinMind) can actually back; every other field this profile can't cover is named in
    `missing_fields` rather than built as a stub. Immediate core, built narrow."""

    name = "get_company_profile"
    description = (
        "Get a partial company-profile card for one stock: sector/industry classification "
        "and the latest announced dividend. Does not include MOPS-sourced identity fields "
        "(chairman/capital/auditor/...) or derived financial ratios — see `missing_fields`."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetCompanyProfileOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "company_profile"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        sector_result = await self._router.fetch(
            Capability.SECTOR_CLASS, symbol, tls_policy=ctx.tls_policy
        )
        dividend_result = await self._router.fetch(
            Capability.DIVIDENDS, symbol, tls_policy=ctx.tls_policy
        )

        sector: str | None = None
        industry: str | None = None
        classification = sector_result.payload.get("classification")
        if isinstance(classification, dict):
            parsed = SectorClassification.model_validate(cast("dict[str, object]", classification))
            sector, industry = parsed.sector, parsed.industry

        latest_dividend: DividendEvent | None = None
        raw_events = dividend_result.payload.get("events")
        if isinstance(raw_events, list) and raw_events:
            rows = cast(_RowList, raw_events)
            latest_dividend = DividendEvent.model_validate(rows[-1])

        return GetCompanyProfileOutput(
            symbol=symbol.id,
            market=symbol.market.value,
            sector=sector,
            industry=industry,
            latest_dividend=latest_dividend,
            sector_caveat=sector_result.currentness_caveat,
            dividend_caveat=dividend_result.currentness_caveat,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table.

    `get_company_profile` is a named immediate-core tool, built narrow here (see its
    class docstring). `get_price_quote` backs the basic price-quote read that's also part of the
    immediate-core set. Everything else here is a real, deferred capability.

    `get_etf_money_flow` reads the ingest-owned Parquet store directly (see its class
    docstring for why) rather than the router, so it needs its own `ParquetStore` built from
    `config.settings` here rather than the router this function otherwise only threads through.
    """
    store = ParquetStore(root=get_settings().parquet_root)
    tools: list[tuple[Tool, bool]] = [
        (GetCompanyProfileTool(router), True),
        (GetPriceQuoteTool(router), True),
        (GetDividendScheduleTool(router), False),
        (GetInstitutionalFlowTool(router), False),
        (GetChipFlowsTool(router), False),
        (GetActiveEtfReportTool(router), False),
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
