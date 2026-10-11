"""The active-ETF holdings report."""

from __future__ import annotations

from datetime import date as date_
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from ingest.jobs import STOCK_INFO_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import ActiveEtfHoldingChangeRow, ActiveEtfHoldingRow, StockInfoRow
from schemas.enums import Capability, DataClass
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools._rows import validate_rows
from tools.base import BaseTool, ToolContext, parse_args
from tools.common import SymbolInput
from tools.market._common import (
    ETF_AUM_CAVEAT,
    HoldingStatus,
    UntypedDict,
    classify_holding_status,
    closes_by_symbol,
    etf_aum_by_symbol,
    latest_dataset_date,
    prior_report_date,
)

_ETF_REPORT_LOOKBACK_DAYS = 10  # bounded per-symbol window to find "target day + prior day"


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


class ActiveEtfHoldingWeightRow(BaseModel):
    """One constituent's FULL current-day weight — every holding on `report_date`, not just the
    ones that changed (`ActiveEtfReportRow`'s own scope). This is the 成分股/產業分佈 tab's real
    backing: a 成分股 donut/table needs the whole holdings list with weight, and a 產業分佈 view
    needs each holding's industry — neither is expressible from the diffed change rows alone.
    `industry_category` is joined from the same whole-market `STOCK_INFO_JOB` roster
    `GetSectorHeatmapTool` already reads (ingest-owned store, never a live per-symbol fetch);
    `None` when the constituent isn't found in that day's roster (never fabricated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_stock_id: str
    component_stock_name: str
    weight: float
    industry_category: str | None = None


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
    # Set only when `fund_size` fell back to `etf_aum` (Yahoo's totalAssets) because today's
    # constituent closes haven't ingested yet — see `_fund_size_with_aum_fallback`'s docstring.
    # `fund_size_prior`/`fund_size_change_pct` are never computed from this fallback source
    # (see that function for why), so this caveat also explains their absence.
    fund_size_caveat: str | None = None
    premium_discount_pct: float | None = None  # 折溢價 (official NAV) — always None, see reason
    premium_discount_unavailable_reason: str | None = _PREMIUM_DISCOUNT_UNAVAILABLE_REASON
    # 估算淨值/折溢價 — self-computed from holdings x close ÷ units_outstanding, NOT official NAV;
    # carries _ESTIMATED_NAV_CAVEAT whenever either is non-None. None when units_outstanding or
    # the ETF's own close is unavailable (never fabricated).
    estimated_nav_per_unit: float | None = None
    estimated_premium_discount_pct: float | None = None
    estimation_caveat: str | None = None
    rows: tuple[ActiveEtfReportRow, ...] = Field(default_factory=tuple)
    # Full current-day holdings (成分股/產業分佈 tabs) — see `ActiveEtfHoldingWeightRow`'s own
    # docstring for why this is a separate field from `rows`.
    current_holdings: tuple[ActiveEtfHoldingWeightRow, ...] = Field(default_factory=tuple)


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
    status = classify_holding_status(
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
    closes = closes_by_symbol(store, market, trade_date)
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


def _fund_size_with_aum_fallback(
    store: ParquetStore,
    market: str,
    etf_symbol: str,
    computed: tuple[float | None, float | None, float | None],
) -> tuple[float | None, float | None, float | None, str | None]:
    """Falls back to `etf_aum` (Yahoo's own `totalAssets`, already ingested — see
    `etf_aum_by_symbol`) only when the shares-x-close computation came back `None` — typically
    because today's constituent closes haven't landed in `TaiwanStockPrice` yet (a same-day
    ingest-timing gap, not a data-availability gap: the AUM figure genuinely exists). Never
    falls back `fund_size_prior`/`fund_size_change_pct`: `etf_aum` is refreshed weekly, not
    daily (confirmed against real ingested data — the same value repeats across consecutive
    days), so a day-over-day delta computed from it would be a fabricated signal, not a real
    one — those two fields stay honestly `None` when the fallback is used."""
    fund_size, fund_size_prior, fund_size_change_pct = computed
    if fund_size is not None:
        return fund_size, fund_size_prior, fund_size_change_pct, None
    aum = etf_aum_by_symbol(store, market).get(etf_symbol)
    if aum is None:
        return fund_size, fund_size_prior, fund_size_change_pct, None
    return aum, None, None, ETF_AUM_CAVEAT


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
    market_price = closes_by_symbol(store, etf_symbol.market.value, report_date).get(etf_symbol.id)
    premium_discount_pct = (
        market_price / nav_per_unit - 1.0 if market_price is not None and nav_per_unit else None
    )
    return nav_per_unit, premium_discount_pct


def _active_etf_current_holdings(
    store: ParquetStore,
    market: str,
    report_holdings: dict[str, ActiveEtfHoldingRow],
) -> tuple[ActiveEtfHoldingWeightRow, ...]:
    """Every constituent's weight on `report_date` (`report_holdings`, already fetched by the
    caller), joined against the whole-market `STOCK_INFO_JOB` roster for `industry_category` —
    same ingest-owned-store join `GetSectorHeatmapTool` already performs, never a live per-symbol
    fetch. Sorted by weight descending so the caller never re-sorts for its own donut/table."""
    if not report_holdings:
        return ()
    roster_date = latest_dataset_date(store, STOCK_INFO_JOB.dataset, market)
    industry_by_symbol: dict[str, str] = {}
    if roster_date is not None:
        raw_roster = store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        industry_by_symbol = {
            row.symbol: row.industry_category for row in rows_from_store(StockInfoRow, raw_roster)
        }
    rows = [
        ActiveEtfHoldingWeightRow(
            component_stock_id=holding.component_stock_id,
            component_stock_name=holding.component_stock_name,
            weight=holding.weight,
            industry_category=industry_by_symbol.get(holding.component_stock_id),
        )
        for holding in report_holdings.values()
    ]
    return tuple(sorted(rows, key=lambda row: row.weight, reverse=True))


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
        prior_date = prior_report_date(dates, report_date)

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
        fund_size, fund_size_prior, fund_size_change_pct, fund_size_caveat = (
            _fund_size_with_aum_fallback(
                self._store,
                symbol.market.value,
                symbol.id,
                _active_etf_fund_size_fields(
                    self._store,
                    symbol.market.value,
                    (report_date, prior_date),
                    (report_holdings, prior_holdings),
                ),
            )
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
        current_holdings = _active_etf_current_holdings(
            self._store, symbol.market.value, report_holdings
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
            fund_size_caveat=fund_size_caveat,
            estimated_nav_per_unit=estimated_nav_per_unit,
            estimated_premium_discount_pct=estimated_premium_discount_pct,
            estimation_caveat=estimation_caveat,
            rows=tuple(rows),
            current_holdings=current_holdings,
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
        units = cast(UntypedDict, raw_info).get("units_outstanding")
        return units if isinstance(units, int) else None
