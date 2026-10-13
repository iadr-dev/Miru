"""The ETF list (AUM, expense ratio, distribution, returns)."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from dataclasses import dataclass
from datetime import date as date_
from datetime import timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from analytics.symbol_universe import SymbolType, classify_symbol_type
from ingest.jobs import (
    ACTIVE_ETF_HOLDING_JOB,
    DIVIDENDS_JOB,
    ETF_EXPENSE_RATIO_DATASET,
    PRICE_EOD_JOB,
)
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import ActiveEtfHoldingRow, DividendEvent, EtfExpenseInfo
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, parse_args
from tools.market._common import (
    ETF_AUM_CAVEAT,
    closes_by_symbol,
    etf_aum_by_symbol,
    etf_nav_names,
    latest_dataset_date,
    real_calendar,
    two_day_window,
    unavailable_reason_for_window,
)

# TWSE's own naming for how often an ETF pays out.
DistributionFrequency = Literal["月配", "季配", "半年配", "年配", "不配息"]

_ETF_DISTRIBUTION_FREQUENCY_BY_EVENT_COUNT: dict[int, DistributionFrequency] = {
    12: "月配",
    4: "季配",
    2: "半年配",
    1: "年配",
    0: "不配息",
}


_TTM_DIVIDEND_LOOKBACK_DAYS = 396  # ~13 months — slack past the exact 12-month cutoff below


def _etf_distribution_frequency(event_count: int) -> DistributionFrequency | None:
    """Classify a trailing-12-month cash-distribution event count into TWSE's own naming
    convention (月配/季配/半年配/年配/不配息) — an OBSERVED-cadence classification, never the
    issuer's declared distribution policy (which this codebase has no source for). `None` for
    any count that doesn't match a clean cadence (e.g. 3 events in 12 months from a fund that
    changed its distribution schedule mid-year) rather than guessing the nearest bucket."""
    return _ETF_DISTRIBUTION_FREQUENCY_BY_EVENT_COUNT.get(event_count)


def _etf_ttm_distribution_by_symbol(
    store: ParquetStore, market: str, as_of: date_
) -> dict[str, tuple[float, int]]:
    """Every ETF-shaped symbol's trailing-12-month cash-distribution total and event count,
    read from the already-ingested `dividends` (`TaiwanStockDividend`) partition tree —
    `DividendEvent.cash_ex_dividend_date` is the real ex-dividend date FinMind reports, filtered
    to the trailing 12 months from `as_of` (never the whole partition's history, so a long-listed
    fund's ancient distributions don't inflate today's yield). Symbols are ETF-filtered via
    `classify_symbol_type` — this dataset's whole-market form covers common stocks too, and
    those must never leak into an ETF-only column.

    Reads only the partition dates the ingest job actually wrote within the lookback window
    (`list_partition_dates`, not a continuous business-day scan) since dividend announcements
    land on scattered exact dates, not a fixed cadence — most days in the window have no
    partition to read at all."""
    # Partition-selection window (~13 months, generous slack) is broader than the exact
    # 12-month cutoff applied to each event's own `cash_ex_dividend_date` below — a partition
    # written within the 13-month scan can still carry an event older than 12 months
    # (`announced_date`/`cash_ex_dividend_date` can diverge), so the real cutoff is enforced
    # per-event, not by partition selection alone.
    partition_window_start = as_of - timedelta(days=_TTM_DIVIDEND_LOOKBACK_DAYS)
    ttm_cutoff = as_of - timedelta(days=365)
    known_dates = [
        d
        for d in store.list_partition_dates(DIVIDENDS_JOB.dataset, market)
        if partition_window_start <= d <= as_of
    ]
    if not known_dates:
        return {}
    raw_rows = store.read_range(DIVIDENDS_JOB.dataset, market, known_dates)
    events = rows_from_store(DividendEvent, raw_rows)
    by_symbol: dict[str, list[float]] = {}
    for event in events:
        if classify_symbol_type(event.symbol) is SymbolType.STOCK:
            continue
        ex_date = event.cash_ex_dividend_date
        cash = event.cash_dividend
        if ex_date is None or ex_date < ttm_cutoff or cash is None or cash <= 0:
            continue
        by_symbol.setdefault(event.symbol, []).append(cash)
    return {symbol: (sum(amounts), len(amounts)) for symbol, amounts in by_symbol.items()}


_ACTIVE_ETF_LOOKBACK_DAYS = 10  # same window scoring_batch.py's own active-ETF gate reads


def _active_etf_symbols_in_window(store: ParquetStore, market: str, as_of: date_) -> set[str]:
    """Which ETF symbols had at least one constituent-holdings snapshot in the trailing window —
    the same ground truth `scoring_batch.py::_active_etf_symbols` uses for its own management-
    activity factor (`ActiveEtfHoldingRow` is an active-ETF-only FinMind capability; a passive/
    leveraged/inverse ETF is never present here). Never derived from a fund name's own "主動"
    marketing prefix — that string is issuer copy, not verified fund-structure data."""
    known_dates = [
        d
        for d in store.list_partition_dates(ACTIVE_ETF_HOLDING_JOB.dataset, market)
        if as_of - timedelta(days=_ACTIVE_ETF_LOOKBACK_DAYS) <= d <= as_of
    ]
    if not known_dates:
        return set()
    raw_rows = store.read_range(ACTIVE_ETF_HOLDING_JOB.dataset, market, known_dates)
    return {row.symbol for row in rows_from_store(ActiveEtfHoldingRow, raw_rows)}


class EtfListRow(BaseModel):
    """One row of the whole-market ETF list — see `GetEtfListTool`'s own docstring for exactly
    which already-ingested dataset backs each field. A field is `None` when its backing dataset
    has no row for this symbol on this date — never fabricated to fill the column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    name: str
    close: float | None = None
    change_abs: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    turnover: float | None = None
    return_1w_pct: float | None = None
    distribution_yield_ttm_pct: float | None = None
    distribution_frequency: DistributionFrequency | None = None  # None if unclassified
    aum: float | None = None
    expense_ratio_pct_approx: float | None = None
    symbol_type: SymbolType  # never STOCK: this list is ETFs only
    # True when this symbol had at least one constituent-holdings snapshot in
    # `ACTIVE_ETF_HOLDING_JOB`'s recent lookback window — the same "is this an active ETF"
    # ground truth `scoring_batch.py::_active_etf_symbols` already uses (an active-ETF-only
    # FinMind capability; a passive/leveraged/inverse ETF never has a row here). Never derived
    # from the fund name's own "主動"-prefix convention, which is a marketing string, not data.
    is_active_etf: bool = False


class GetEtfListInput(BaseModel):
    """A market + an optional trading day; omitted/`None` means "latest available" — same
    semantics as `GetAdvanceDeclineInput`/`GetSectorHeatmapInput`. No sort/paging args: the
    whole ETF universe (~300 rows, bounded) returns in one call and the caller sorts — this is
    not a full-market-scan violation since it's a bounded subset read from the ingest-owned
    store, the same posture `GetSectorHeatmapTool` already has for every industry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report the ETF list for (ISO date). Omitted or null means "
        "the latest available trading day.",
    )


_ETF_EXPENSE_RATIO_CAVEAT = (
    "費用率 is a management+custody fee floor from SITCA's monthly disclosure "
    "(EtfExpenseInfo.total_expense_ratio_pct_approx) — NOT the audited total expense ratio; "
    "other operating costs are not included."
)


_ETF_DISTRIBUTION_CAVEAT = (
    "配息率/頻率 are inferred from the trailing-12-month cadence of real cash-distribution "
    "events — an observed-cadence classification, not the issuer's declared distribution "
    "policy, and approximate for a fund listed under 12 months."
)


class GetEtfListOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    currentness_caveat: str = Field(
        default="Price/change/volume are end-of-day, not live intraday.", min_length=1
    )
    aum_caveat: str = Field(default=ETF_AUM_CAVEAT, min_length=1)
    expense_ratio_caveat: str = Field(default=_ETF_EXPENSE_RATIO_CAVEAT, min_length=1)
    distribution_caveat: str = Field(default=_ETF_DISTRIBUTION_CAVEAT, min_length=1)
    rows: tuple[EtfListRow, ...] = Field(default_factory=tuple)


_MIN_RETURN_SERIES_LENGTH = 2  # need at least first-vs-last close to compute a return


def _etf_week_closes(
    store: ParquetStore,
    market: str,
    calendar: TradingDayCalendar,
    trade_date: date_,
    etf_symbols: set[str],
) -> dict[str, list[tuple[date_, float]]]:
    """Every ETF symbol's ascending-by-date closes over the trailing 5-trading-day window —
    the input `_etf_list_rows`' `return_1w_pct` needs (first-vs-last close), read once for the
    whole universe rather than per-symbol."""
    week_start = calendar.shift_business_days(trade_date, -5)
    week_dates = calendar.business_days_between(week_start, trade_date)
    week_raw = store.read_range(PRICE_EOD_JOB.dataset, market, week_dates)
    by_symbol: dict[str, list[tuple[date_, float]]] = {}
    for row in week_raw:
        symbol = row.get("symbol")
        close = row.get("close")
        # `trade_date` (a string) is the row's own field; `date` is `read_range`'s partition
        # metadata column and is already a `datetime.date`, not a string — reading `date` here
        # instead of `trade_date` silently dropped every row (`isinstance(..., str)` never
        # matched a `date` object), so `return_1w_pct` came back `None` for every ETF.
        row_date_raw = row.get("trade_date")
        if not (
            isinstance(symbol, str)
            and symbol in etf_symbols
            and isinstance(close, int | float)
            and isinstance(row_date_raw, str)
        ):
            continue
        by_symbol.setdefault(symbol, []).append((date_.fromisoformat(row_date_raw), float(close)))
    return by_symbol


def _etf_expense_ratios_by_symbol(store: ParquetStore, market: str) -> dict[str, float]:
    expense_date = latest_dataset_date(store, ETF_EXPENSE_RATIO_DATASET, market)
    if expense_date is None:
        return {}
    raw_expense = store.read_partition(ETF_EXPENSE_RATIO_DATASET, market, expense_date)
    return {
        row.symbol: row.total_expense_ratio_pct_approx
        for row in rows_from_store(EtfExpenseInfo, raw_expense)
        if row.total_expense_ratio_pct_approx is not None
    }


@dataclass(frozen=True)
class _EtfListJoinInputs:
    """Every already-ingested dataset `_etf_list_rows` joins by symbol — bundled so
    `GetEtfListTool.run` reads as one `_gather_etf_list_inputs(...)` call instead of five
    separate assignments, keeping the caller's own branch/statement count low."""

    prior_closes: dict[str, float]
    week_closes: dict[str, list[tuple[date_, float]]]
    names: dict[str, str]
    aum: dict[str, float]
    expense_ratios: dict[str, float]
    distributions: dict[str, tuple[float, int]]
    active_etf_symbols: set[str]


@dataclass(frozen=True)
class _EtfListWindow:
    """The resolved dates/calendar `_gather_etf_list_inputs` needs — bundled so that function
    stays under the max-positional-arguments lint limit without losing any named parameter."""

    calendar: TradingDayCalendar
    trade_date: date_
    prior_date: date_


def _gather_etf_list_inputs(
    store: ParquetStore, market: str, window: _EtfListWindow, etf_symbols: set[str]
) -> _EtfListJoinInputs:
    return _EtfListJoinInputs(
        prior_closes=closes_by_symbol(store, market, window.prior_date),
        week_closes=_etf_week_closes(
            store, market, window.calendar, window.trade_date, etf_symbols
        ),
        names=etf_nav_names(store, market),
        aum=etf_aum_by_symbol(store, market),
        expense_ratios=_etf_expense_ratios_by_symbol(store, market),
        distributions=_etf_ttm_distribution_by_symbol(store, market, window.trade_date),
        active_etf_symbols=_active_etf_symbols_in_window(store, market, window.trade_date),
    )


def _etf_list_row(
    symbol: str, bar: dict[str, object], market: str, inputs: _EtfListJoinInputs
) -> EtfListRow:
    """One `EtfListRow`, joining `bar` (today's `price_eod` row) against every already-gathered
    input by symbol — pulled out of `GetEtfListTool.run`'s per-symbol loop purely to keep that
    loop's own branch/statement count low; no behavior of its own beyond the field-by-field
    join each line already documents."""
    close = bar.get("close")
    close_f = float(close) if isinstance(close, int | float) else None
    prior_close = inputs.prior_closes.get(symbol)
    change_abs = close_f - prior_close if close_f is not None and prior_close else None
    change_pct = change_abs / prior_close * 100 if change_abs is not None and prior_close else None
    volume = bar.get("volume")
    turnover = bar.get("turnover")

    week_series = sorted(inputs.week_closes.get(symbol, []))
    return_1w_pct: float | None = None
    if len(week_series) >= _MIN_RETURN_SERIES_LENGTH:
        first_close, last_close = week_series[0][1], week_series[-1][1]
        if first_close:
            return_1w_pct = (last_close / first_close - 1) * 100

    # Absent from `inputs.distributions` means "no real cash-distribution event found within
    # the lookback window for this symbol" — distinct from "confirmed zero events", which would
    # be a real, legitimate 不配息 classification. This tool cannot tell the two apart (a fund
    # with zero real history within the window looks identical to one with a genuinely empty
    # `dividends` partition tree), so both fields stay `None` rather than asserting 不配息 for
    # every symbol this loop simply never saw a distribution row for.
    distribution = inputs.distributions.get(symbol)
    distribution_yield_ttm_pct: float | None = None
    distribution_frequency: DistributionFrequency | None = None
    if distribution is not None:
        dist_total, dist_count = distribution
        distribution_yield_ttm_pct = dist_total / close_f * 100 if dist_count and close_f else None
        distribution_frequency = _etf_distribution_frequency(dist_count)

    return EtfListRow(
        symbol=symbol,
        market=market,
        name=inputs.names.get(symbol, symbol),
        close=close_f,
        change_abs=change_abs,
        change_pct=change_pct,
        volume=int(volume) if isinstance(volume, int | float) else None,
        turnover=float(turnover) if isinstance(turnover, int | float) else None,
        return_1w_pct=return_1w_pct,
        distribution_yield_ttm_pct=distribution_yield_ttm_pct,
        distribution_frequency=distribution_frequency,
        aum=inputs.aum.get(symbol),
        expense_ratio_pct_approx=inputs.expense_ratios.get(symbol),
        symbol_type=classify_symbol_type(symbol),
        is_active_etf=symbol in inputs.active_etf_symbols,
    )


class GetEtfListTool(SyncStoreTool):
    """`get_etf_list` — the whole-market TW ETF list (~300 symbols): price/change/volume/
    1-week-return from `PRICE_EOD_JOB`, name from `EtfNavRow.fund_name` (`etf_nav`), AUM from
    `EtfAumRow.aum` (`etf_aum`, Yahoo's own `totalAssets`), expense-ratio floor from
    `EtfExpenseInfo` (`etf_expense_ratio`), trailing-12-month distribution yield/frequency
    derived from `DividendEvent` rows (`dividends`), and `is_active_etf` from a recent
    `ACTIVE_ETF_HOLDING_JOB` snapshot (see `_active_etf_symbols_in_window`'s own docstring) —
    five already-ingested datasets joined by symbol, never a per-symbol live fan-out (same
    whole-market-aggregate posture `GetSectorHeatmapTool`'s own docstring documents). 原型/
    槓桿型/反向型 is `symbol_type` (already present, from `classify_symbol_type`'s code-shape
    rule) — `is_active_etf` is the orthogonal 主動式/被動式 axis, since a leveraged/inverse ETF
    is passive by construction and a plain ("原型") ETF can be either.

    The ETF universe itself is derived purely from `PRICE_EOD_JOB`'s own symbol codes via
    `analytics.symbol_universe.classify_symbol_type` (00-prefixed, ≤6 chars) — no separate
    roster fetch, and no investment-region column (no free authoritative source publishes a
    normalized geography tag for listed TW ETFs; `EtfNavRow`'s own `fund_name`/`EtfFundInfo`'s
    `tracked_index` are raw strings, not a region taxonomy) and no total-return-since-inception
    (no whole-market baseline source exists; computing it would need a per-symbol live fetch
    across the whole ETF universe on every request, which the no-full-market-scan governance
    rule forbids).

    Public tier: `Capability.PRICE_EOD`/`STOCK_INFO` are `DataClass.PUBLIC` (see
    `GetSectorHeatmapTool`'s own tier reasoning); `etf_nav`/`etf_aum`/`etf_expense_ratio`/
    `dividends` are likewise ingested PUBLIC-tier datasets — no Sponsor gate applies here."""

    name = "get_etf_list"
    description = (
        "Get the whole-market Taiwan-listed ETF list for one trading day (defaults to the "
        "latest available day): price, change, volume, 1-week return, AUM, expense-ratio "
        "floor, and trailing-12-month distribution yield/frequency for every listed ETF."
    )
    input_model: type[BaseModel] = GetEtfListInput
    output_model: type[BaseModel] = GetEtfListOutput
    data_class = DataClass.PUBLIC
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "etf_list"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetEtfListInput, args)
        market = typed.market.value
        calendar = real_calendar(self._store, market)
        window = two_day_window(
            self._store, PRICE_EOD_JOB.dataset, market, calendar, typed.trade_date
        )
        if window is None:
            return GetEtfListOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window

        today_bars = self._store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
        etf_bars_today: dict[str, dict[str, object]] = {
            cast("str", row["symbol"]): row
            for row in today_bars
            if isinstance(row.get("symbol"), str)
            and classify_symbol_type(cast("str", row["symbol"])) is not SymbolType.STOCK
        }
        inputs = _gather_etf_list_inputs(
            self._store,
            market,
            _EtfListWindow(calendar=calendar, trade_date=trade_date, prior_date=prior_date),
            set(etf_bars_today),
        )
        rows = tuple(
            _etf_list_row(symbol, bar, market, inputs) for symbol, bar in etf_bars_today.items()
        )

        return GetEtfListOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            rows=rows,
        )
