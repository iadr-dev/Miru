"""`run_screener` — the gates→setup→rank funnel engine (README.md §1). Given a market, a set of
AND/OR conditions (or a named preset), and gate overrides, returns the survivor list plus the
funnel's stage counts.

**Stage ⓐ (gates)**: binary elimination against `gates.SCREENER_GATES` — liquidity/price/market-cap
floors plus 處置/注意/全額交割 exclusion. Reads the latest already-ingested price/attention/
disposition partitions; never a live per-symbol fetch (governance §1.3/§6, no full-market scan).

**Stage ⓑ (setup)**: evaluates the requested conditions against the gated universe.
- TW: boolean tags dispatch to the existing `screener/store.py` flag-partition match; numeric
  conditions filter the already-batch-computed `stock_scores` cache (`scoring_store.
  get_all_scores_for_date` — a bulk read of an already-materialized table, not a live scan).
- US: builds a `yf.EquityQuery`/`ETFQuery` AND/OR tree via `dataaccess/screener.py`.
- ETF: filters the already-ingested `etf_nav`/`etf_aum` whole-market partitions in-memory.

**Stage ⓒ (rank)** is intentionally NOT this module's job — top-N ranking is `get_top_stocks`/
`get_leaderboard`'s existing read (see `tools/score_tools.py`), kept as a separate call the
frontend's `RankControl` triggers after a screen's survivors are known (README.md §5.3). This
keeps "which symbols match" (a filter, always available) cleanly separate from "how are they
ordered" (a ranking, only meaningful for the subset that already matched) — never conflating the
two, which is also the exact governance line ("符合條件的清單，非推薦名單" vs. a ranked list).

`count_only=True` short-circuits after computing `len(survivors)` — skipping row hydration (the
gate-bar/builder's live survivor-count probe, README.md §4/§5.1, debounced on every condition
edit; keeping this path cheap is what makes that live count viable calling on every keystroke)."""

from __future__ import annotations

import asyncio
import functools
from calendar.trading_days import TradingDayCalendar
from collections.abc import Awaitable, Callable, Sequence
from datetime import date as date_
from datetime import timedelta
from typing import Literal, cast

import anyio.to_thread
import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.fundamentals import (
    BALANCE_SHEET_DATASET,
    FINANCIALS_DATASET,
    ROE_INPUT_ITEMS,
    compute_fundamental_metrics,
)
from analytics.indicators import PriceBar, kd, macd, rsi, sma
from analytics.models import ScoreProfile, ScoreResult
from analytics.scoring_batch import ETF_EXPENSE_RATIO_DATASET
from analytics.scoring_batch.inputs import TW_STATEMENT_COLUMNS, lazy_statement_lines
from analytics.scoring_store import get_all_scores_for_date
from analytics.screener.batch import SCREENER_FLAGS_DATASET
from analytics.screener.catalog import tag_keys
from analytics.screener.factor_catalog import FactorField, get_factor_catalog
from analytics.screener.gates import GateDefaults, GateOverrides, resolve_gates
from analytics.symbol_universe import (
    SymbolType,
    classify_symbol_type,
    is_scoreable_symbol,
    scoreable_symbol_sql,
)
from dataaccess.screener import run_equity_screener
from ingest.jobs import DIVIDENDS_JOB
from ingest.reader import ParquetMarketDataReader
from ingest.rows import rows_from_store
from ingest.store import ParquetStore, sql_in
from schemas.capabilities import DividendEvent, EtfExpenseInfo, FinancialStatementLine
from schemas.enums import Market
from schemas.screener import ScreenerAssetKind, ScreenerCondition, ScreenerOperator, ScreenerQuery

logger = structlog.get_logger(__name__)

_PRICE_EOD_DATASET = "TaiwanStockPrice"
_ATTENTION_STOCKS_DATASET = "attention_stocks"
_DISPOSITION_EVENTS_DATASET = "disposition_events"
_ETF_NAV_DATASET = "etf_nav"
_ETF_AUM_DATASET = "etf_aum"

_ETF_DISTRIBUTION_FREQUENCY_BY_EVENT_COUNT: dict[int, str] = {
    12: "月配",
    4: "季配",
    2: "半年配",
    1: "年配",
    0: "不配息",
}
_TTM_DIVIDEND_LOOKBACK_DAYS = 396  # ~13 months — slack past the exact 12-month cutoff below
_MIN_RETURN_SERIES_LENGTH = 2  # need at least first-vs-last close to compute a return


class ScreenerConditionInput(BaseModel):
    """One condition row, referencing a `factor_catalog` key (not a raw upstream field name —
    the engine resolves `factor` -> `source_field` via the catalog, so a caller never needs to
    know yfinance's/stock_scores' own column names)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: str = Field(min_length=1)
    op: ScreenerOperator
    value: float | str | None = None
    value2: float | str | None = None


class RunScreenerInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["TW", "US", "ETF"]
    conditions: tuple[ScreenerConditionInput, ...] = Field(default_factory=tuple)
    match: Literal["AND", "OR"] = "AND"
    gate_overrides: GateOverrides | None = None
    count_only: bool = False
    size: int = Field(default=50, ge=1, le=250)


class ScreenerSurvivor(BaseModel):
    """One row surviving gates+setup. `matched_conditions` names which requested conditions this
    symbol satisfied (AND: all of them; OR: the satisfied subset) — same "show the work"
    transparency rule `TagScreenerHit.matched_tags` already uses.

    `display` carries a small set of already-fetched quote fields for the results table to
    render without a second per-symbol lookup — populated only for US (Yahoo's screener response
    already includes `regularMarketPrice`/`marketCap`/etc. for every hit; TW/ETF survivors get
    their price/volume/sector from the existing `useSymbolQuotes` batched read instead, so this
    stays empty for those markets rather than duplicating that path)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    matched_conditions: tuple[str, ...] = Field(default_factory=tuple)
    display: dict[str, float | str | bool | None] = Field(default_factory=dict)


class FunnelCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    universe: int = Field(ge=0)
    gated: int = Field(ge=0)
    setup: int = Field(ge=0)


class RunScreenerOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    funnel: FunnelCounts
    survivors: tuple[ScreenerSurvivor, ...] = Field(default_factory=tuple)
    detail: str = Field(min_length=1)
    degraded: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Human-readable notes on any condition this pass could not evaluate (e.g. "
        "an unknown factor key) — never silently dropped.",
    )


def _latest_price_partition_date(store: ParquetStore, market: str) -> date_ | None:
    dates = store.list_partition_dates(_PRICE_EOD_DATASET, market)
    return max(dates) if dates else None


def _gated_tw_universe(store: ParquetStore, gates: GateDefaults) -> tuple[set[str], date_ | None]:
    """The set of TW symbols surviving stage ⓐ, read from the latest already-ingested price
    partition plus the current day's attention/disposition feeds (forward-only feeds — see
    `AttentionStockRow`/`DispositionEventRow`'s own docstrings for why these are "today only",
    which is what the gate needs: "is this symbol under an active flag right now")."""
    as_of = _latest_price_partition_date(store, Market.TW.value)
    if as_of is None:
        return set(), None
    price_rows = store.read_partition(_PRICE_EOD_DATASET, Market.TW.value, as_of)
    survivors: set[str] = set()
    for row in price_rows:
        symbol = row.get("symbol")
        close = row.get("close")
        volume = row.get("volume")
        if not isinstance(symbol, str) or not symbol:
            continue
        # TW tab is stocks-only — TW-listed ETFs have their own "ETF" market tab
        # (`SCREENER_MARKETS`), and warrants/derivatives are never scoreable at all.
        if not is_scoreable_symbol(symbol, Market.TW):
            continue
        if classify_symbol_type(symbol, Market.TW) is not SymbolType.STOCK:
            continue
        if isinstance(close, int | float) and close < gates.min_price:
            continue
        if isinstance(volume, int | float) and volume < gates.min_avg_volume:
            continue
        survivors.add(symbol)

    if gates.exclude_attention:
        attention_dates = store.list_partition_dates(_ATTENTION_STOCKS_DATASET, Market.TW.value)
        if attention_dates:
            flagged = {
                row.get("symbol")
                for row in store.read_partition(
                    _ATTENTION_STOCKS_DATASET, Market.TW.value, max(attention_dates)
                )
            }
            survivors -= {s for s in flagged if isinstance(s, str)}

    if gates.exclude_disposition:
        disposition_dates = store.list_partition_dates(_DISPOSITION_EVENTS_DATASET, Market.TW.value)
        if disposition_dates:
            flagged = {
                row.get("symbol")
                for row in store.read_partition(
                    _DISPOSITION_EVENTS_DATASET, Market.TW.value, max(disposition_dates)
                )
            }
            survivors -= {s for s in flagged if isinstance(s, str)}

    return survivors, as_of


def _etf_expense_ratios_by_symbol(store: ParquetStore, market: str) -> dict[str, float]:
    """Every ETF's latest disclosed expense-ratio floor — same `etf_expense_ratio` partition and
    `EtfExpenseInfo.total_expense_ratio_pct_approx` field `get_etf_list` already bulk-joins
    (`tools/market/etf_list.py::_etf_expense_ratios_by_symbol`), duplicated here rather than
    imported
    since that function lives in the `tools/` layer, which `analytics/` may not import from
    (dependency rule, `backend/CLAUDE.md` §3)."""
    dates = store.list_partition_dates(ETF_EXPENSE_RATIO_DATASET, market)
    if not dates:
        return {}
    raw = store.read_partition(ETF_EXPENSE_RATIO_DATASET, market, max(dates))
    return {
        row.symbol: row.total_expense_ratio_pct_approx
        for row in rows_from_store(EtfExpenseInfo, raw)
        if row.total_expense_ratio_pct_approx is not None
    }


def _etf_distribution_frequency(event_count: int) -> str | None:
    """Same TWSE-convention cadence classification `tools/market/etf_list.py::
    _etf_distribution_frequency` documents — an OBSERVED-cadence read from real cash-distribution
    events, never the issuer's declared policy. `None` for a count that doesn't match a clean
    cadence, rather than guessing the nearest bucket."""
    return _ETF_DISTRIBUTION_FREQUENCY_BY_EVENT_COUNT.get(event_count)


def _etf_ttm_distribution_by_symbol(
    store: ParquetStore, market: str, as_of: date_
) -> dict[str, tuple[float, int]]:
    """Every ETF-shaped symbol's trailing-12-month cash-distribution total and event count — same
    computation `tools/market/etf_list.py::_etf_ttm_distribution_by_symbol` documents in full,
    duplicated here for the same dependency-rule reason as `_etf_expense_ratios_by_symbol` above."""
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


# Every ETF trailing-return window this catalog exposes, trading-day counts (a real trading
# calendar, not calendar days): ~1 week/month/quarter/half-year/year. Keyed by the `FactorField`
# key `_ETF_RETURN_WINDOW_SOURCE_FIELDS` maps each to below.
_ETF_RETURN_WINDOWS_TRADING_DAYS: dict[str, int] = {
    "return_1w_pct": 5,
    "return_1m_pct": 21,
    "return_1q_pct": 63,
    "return_6m_pct": 126,
    "return_1y_pct": 250,
}


def _etf_trailing_returns_by_symbol(
    store: ParquetStore, market: str, as_of: date_, etf_symbols: set[str]
) -> dict[str, dict[str, float]]:
    """Every ETF's price return over EVERY window in `_ETF_RETURN_WINDOWS_TRADING_DAYS`, from one
    shared `price_eod` read spanning the longest window (~1 year) — first-vs-last close within
    each shorter window's own slice of that same series, never a separate read per window. A
    plain `TradingDayCalendar()`, the same naive Mon-Fri-minus-holidays instance
    `tools/market/_common.py::real_calendar` uses for this exact purpose. `symbols=etf_symbols`
    makes
    `read_range` filter at the DuckDB SQL layer rather than materializing all ~2000 TW symbols'
    ~250-day price history (a measured ~500K-row whole-market read) just to keep the ~300 ETF
    rows — the exact blowup `read_range`'s own docstring warns an unfiltered multi-day read
    causes, and what turned this factor into a request that never returned in manual testing."""
    calendar = TradingDayCalendar()
    longest_window = max(_ETF_RETURN_WINDOWS_TRADING_DAYS.values())
    year_start = calendar.shift_business_days(as_of, -longest_window)
    year_dates = calendar.business_days_between(year_start, as_of)
    raw_rows = store.read_range(_PRICE_EOD_DATASET, market, year_dates, symbols=tuple(etf_symbols))
    by_symbol: dict[str, list[tuple[date_, float]]] = {}
    for row in raw_rows:
        symbol = row.get("symbol")
        close = row.get("close")
        row_date_raw = row.get("date")
        if not (isinstance(symbol, str) and isinstance(close, int | float)):
            continue
        row_date = row_date_raw if isinstance(row_date_raw, date_) else None
        if row_date is None:
            continue
        by_symbol.setdefault(symbol, []).append((row_date, float(close)))

    returns_by_symbol: dict[str, dict[str, float]] = {}
    for symbol, series in by_symbol.items():
        ordered = sorted(series)
        if len(ordered) < _MIN_RETURN_SERIES_LENGTH:
            continue
        last_close = ordered[-1][1]
        windows: dict[str, float] = {}
        for field_key, trading_days in _ETF_RETURN_WINDOWS_TRADING_DAYS.items():
            window_slice = ordered[-(trading_days + 1) :]
            if len(window_slice) < _MIN_RETURN_SERIES_LENGTH:
                continue
            first_close = window_slice[0][1]
            if first_close:
                windows[field_key] = (last_close / first_close - 1) * 100
        if windows:
            returns_by_symbol[symbol] = windows
    return returns_by_symbol


def _etf_aum_by_symbol(store: ParquetStore) -> dict[str, float]:
    """Every ETF's AUM from the latest `etf_aum` partition (rows without a numeric AUM skipped)."""
    aum_dates = store.list_partition_dates(_ETF_AUM_DATASET, Market.TW.value)
    aum_by_symbol: dict[str, float] = {}
    if aum_dates:
        for row in store.read_partition(_ETF_AUM_DATASET, Market.TW.value, max(aum_dates)):
            symbol = row.get("symbol")
            aum = row.get("aum")
            if isinstance(symbol, str) and isinstance(aum, int | float):
                aum_by_symbol[symbol] = float(aum)
    return aum_by_symbol


def _passes_etf_gates(
    symbol: str, close: float | None, aum: float | None, gates: GateDefaults
) -> bool:
    """The price / leveraged-inverse / AUM gates; an ETF with no close is not price-gated."""
    if close is not None and close < gates.min_price:
        return False
    if gates.exclude_leveraged_inverse and classify_symbol_type(symbol, Market.TW) in (
        SymbolType.ETF_LEVERAGED,
        SymbolType.ETF_INVERSE,
    ):
        return False
    return not (gates.min_aum > 0 and (aum is None or aum < gates.min_aum))


def _etf_distribution_fields(
    distribution: tuple[float, int] | None, close: float | None
) -> tuple[float | None, str | None]:
    """(trailing-12-month yield %, observed cadence) from a (cash total, event count) pair; both
    `None` without a distribution record or a close to divide by."""
    if distribution is None or not close:
        return None, None
    dist_total, dist_count = distribution
    yield_pct = dist_total / close * 100 if dist_count else None
    return yield_pct, _etf_distribution_frequency(dist_count)


def _gated_etf_universe(
    store: ParquetStore, gates: GateDefaults
) -> tuple[dict[str, dict[str, object]], date_ | None]:
    """The gated TW-ETF universe, keyed by symbol -> its merged `etf_nav`/`etf_aum`/
    `etf_expense_ratio`/`dividends`/`price_eod` row (the setup stage's filters read straight off
    this dict, no second store read) — the same joins `get_etf_list` already assembles for its
    own whole-market view, reused here as screener-filterable fields."""
    nav_dates = store.list_partition_dates(_ETF_NAV_DATASET, Market.TW.value)
    if not nav_dates:
        return {}, None
    as_of = max(nav_dates)
    nav_rows = store.read_partition(_ETF_NAV_DATASET, Market.TW.value, as_of)
    etf_symbols = {
        symbol for row in nav_rows if isinstance(symbol := row.get("symbol"), str) and symbol
    }
    aum_by_symbol = _etf_aum_by_symbol(store)
    expense_by_symbol = _etf_expense_ratios_by_symbol(store, Market.TW.value)
    distribution_by_symbol = _etf_ttm_distribution_by_symbol(store, Market.TW.value, as_of)
    returns_by_symbol = _etf_trailing_returns_by_symbol(store, Market.TW.value, as_of, etf_symbols)

    merged: dict[str, dict[str, object]] = {}
    for row in nav_rows:
        symbol = row.get("symbol")
        close = row.get("close_price")
        close_f = float(close) if isinstance(close, int | float) else None
        if not isinstance(symbol, str) or not symbol:
            continue
        aum = aum_by_symbol.get(symbol)
        if not _passes_etf_gates(symbol, close_f, aum, gates):
            continue

        distribution_yield_ttm_pct, distribution_frequency = _etf_distribution_fields(
            distribution_by_symbol.get(symbol), close_f
        )
        merged[symbol] = {
            **row,
            **returns_by_symbol.get(symbol, {}),
            "aum": aum,
            "expense_ratio_pct_approx": expense_by_symbol.get(symbol),
            "distribution_yield_ttm_pct": distribution_yield_ttm_pct,
            "distribution_frequency": distribution_frequency,
            "symbol_type": classify_symbol_type(symbol, Market.TW).value,
        }
    return merged, as_of


def _condition_holds(value: float | None, op: ScreenerOperator, target: object) -> bool:
    if value is None:
        return False
    if op is ScreenerOperator.GT:
        return isinstance(target, int | float) and value > target
    if op is ScreenerOperator.GTE:
        return isinstance(target, int | float) and value >= target
    if op is ScreenerOperator.LT:
        return isinstance(target, int | float) and value < target
    if op is ScreenerOperator.LTE:
        return isinstance(target, int | float) and value <= target
    return False


def _string_condition_holds(value: str | None, op: ScreenerOperator, target: object) -> bool:
    """`eq` for enum-like ETF factors (`symbol_type`, `distribution_frequency`) — a separate
    codepath from `_condition_holds` since those factors carry a string, never a number, and
    `eq` is meaningless against the numeric comparisons above. `ScreenerConditionInput` has no
    `values` list (unlike the yfinance-facing `ScreenerCondition`), so `in` is not wired here —
    the catalog still advertises it (`op_set`) for a future multi-select control, but every
    caller today can only build a single-value `eq` condition."""
    if value is None or op is not ScreenerOperator.EQ:
        return False
    return isinstance(target, str) and value == target


def _score_field_value(result: ScoreResult, source_field: str) -> float | None:
    breakdown = result.breakdown
    factor = getattr(breakdown, source_field, None)
    return factor.percentile if factor is not None else None


_VALUATION_RATIOS_DATASET = "TaiwanStockPER"
_VALUATION_RATIO_SOURCE_FIELDS = frozenset({"per", "pbr", "dividend_yield"})


def _latest_valuation_ratios_by_symbol(
    store: ParquetStore, market: str
) -> dict[str, dict[str, float]]:
    """The latest whole-market `valuation_ratios` snapshot, keyed by symbol -> its `per`/`pbr`/
    `dividend_yield` values — the same already-ingested `VALUATION_RATIOS_JOB` partition
    `_TW_RAW_VALUATION_FACTORS` reads back, read once per screener request rather than re-read
    per condition (a request with 2+ raw-valuation conditions shares one read)."""
    dates = store.list_partition_dates(_VALUATION_RATIOS_DATASET, market)
    if not dates:
        return {}
    raw = store.read_partition(_VALUATION_RATIOS_DATASET, market, max(dates))
    by_symbol: dict[str, dict[str, float]] = {}
    for row in raw:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        values: dict[str, float] = {}
        for field in ("per", "pbr", "dividend_yield"):
            value = row.get(field)
            if isinstance(value, int | float):
                values[field] = float(value)
        if values:
            by_symbol[symbol] = values
    return by_symbol


_EPS_ITEM_NAMES: tuple[str, ...] = ("EPS", "Diluted EPS", "Basic EPS")
# ~5 quarters of statement history, generous slack past the 4 quarters `compute_fundamental_
# metrics`'s TTM sum needs — a filing landing a few weeks late should not silently drop below 4
# available quarters. Statement partitions are written on their own irregular per-filing cadence
# (`ingest/jobs.py`'s `FINANCIALS_JOB`/`BALANCE_SHEET_JOB` docstrings), not one-per-trading-day, so
# this reads the trailing ~450 calendar days of write dates rather than a trading-day count.
_FUNDAMENTALS_LOOKBACK_DAYS = 450


def _latest_period_eps(lines: Sequence[FinancialStatementLine]) -> float | None:
    """This symbol's most recent reported period's EPS line — `_EPS_ITEM_NAMES`'s TW label
    (`"EPS"`) confirmed real and already relied on by
    `tools/market/company_profile.py::_latest_eps`."""
    periods = sorted({line.period_end for line in lines}, reverse=True)
    for period in periods:
        for line in lines:
            if line.period_end == period and line.item in _EPS_ITEM_NAMES:
                return line.value
    return None


def _fundamentals_as_of(store: ParquetStore, market: str) -> date_ | None:
    dates = (
        *store.list_partition_dates(FINANCIALS_DATASET, market),
        *store.list_partition_dates(BALANCE_SHEET_DATASET, market),
    )
    return max(dates) if dates else None


def _latest_fundamentals_by_symbol(store: ParquetStore, market: str) -> dict[str, dict[str, float]]:
    """Every symbol's `roe_ttm`/`eps_latest_quarter`, computed from the whole-market income +
    balance-sheet partitions — reusing `analytics/fundamentals.py::compute_fundamental_metrics`
    (the same ROE formula the per-symbol score path already uses), just grouped by symbol from one
    whole-market read instead of a per-symbol store call. `market_value=None` since ROE needs only
    income-statement + balance-sheet lines — the Sponsor-tier market-cap dataset is not read here,
    so this stays PUBLIC-tier like its two inputs. `as_of` is the latest write date either
    statement partition actually has (never wall-clock "today" — statements land on an irregular
    per-filing cadence, so "today" is almost never a real partition date)."""
    income_all_dates = store.list_partition_dates(FINANCIALS_DATASET, market)
    balance_all_dates = store.list_partition_dates(BALANCE_SHEET_DATASET, market)
    if not income_all_dates and not balance_all_dates:
        return {}
    as_of = max((*income_all_dates, *balance_all_dates))
    window_start = as_of - timedelta(days=_FUNDAMENTALS_LOOKBACK_DAYS)
    income_dates = [d for d in income_all_dates if window_start <= d <= as_of]
    balance_dates = [d for d in balance_all_dates if window_start <= d <= as_of]

    # Only the rows ROE and the latest EPS read (net income, equity, EPS), as columnar frames, and
    # each symbol's lines built when that symbol is computed: the whole window as row dicts and
    # parsed objects was about 1.2M rows (1-2 GB) for a request that needs a few per symbol.
    wanted = sql_in("item", (*ROE_INPUT_ITEMS, *_EPS_ITEM_NAMES))
    lines_by_symbol = lazy_statement_lines(
        [
            store.read_range_frame(
                dataset, market, dates, columns=TW_STATEMENT_COLUMNS, where=wanted
            )
            for dataset, dates in (
                (FINANCIALS_DATASET, income_dates),
                (BALANCE_SHEET_DATASET, balance_dates),
            )
        ]
    )

    by_symbol: dict[str, dict[str, float]] = {}
    for symbol in lines_by_symbol:
        lines = lines_by_symbol[symbol]
        values: dict[str, float] = {}
        roe = compute_fundamental_metrics(lines, market_value=None).roe
        if roe is not None:
            values["roe_ttm"] = roe * 100  # `_safe_ratio` returns a plain fraction, not a percent
        eps = _latest_period_eps(lines)
        if eps is not None:
            values["eps_latest_quarter"] = eps
        if values:
            by_symbol[symbol] = values
    return by_symbol


_FUNDAMENTALS_BATCH_SOURCE_FIELDS = frozenset({"roe_ttm", "eps_latest_quarter"})

# Same 260-trading-day window `analytics/screener/batch.py::_PRICE_LOOKBACK_DAYS` reads for its
# own MA240/52-week-high tag computation — enough history for every indicator here (MA20's own
# warm-up is the shortest; RSI(14)/KD(9,3,3)/MACD(12,26,9) all need far fewer bars than 260).
_TECHNICAL_INDICATOR_LOOKBACK_DAYS = 260
_TECHNICAL_INDICATOR_SOURCE_FIELDS = frozenset(
    {"rsi_14", "ma20_bias_pct", "volume_vs_20d_avg_ratio", "macd_histogram", "kd_k", "kd_d"}
)


def _technical_indicator_values(bars: Sequence[PriceBar]) -> dict[str, float]:
    """One symbol's latest real reading for every `_TW_TECHNICAL_INDICATOR_FACTORS` field, from
    `analytics/indicators.py`'s pure functions over its own already-built bar series — the exact
    same functions `batch.py`'s boolean cross-event tags already call, just reading the literal
    latest value instead of testing for a same-day crossing. A field absent from the result means
    that indicator's own warm-up window (e.g. MA20 needs 20 bars) is not yet satisfied for this
    symbol, never a fabricated 0."""
    values: dict[str, float] = {}
    if not bars:
        return values

    rsi_values = rsi(bars, period=14).values
    if rsi_values and rsi_values[-1] is not None:
        values["rsi_14"] = rsi_values[-1]

    ma20_values = sma(bars, window=20).values
    ma20 = ma20_values[-1] if ma20_values else None
    if ma20 is not None and ma20 != 0:
        values["ma20_bias_pct"] = (bars[-1].close - ma20) / ma20 * 100

    volume_window = 20
    if len(bars) >= volume_window:
        avg_volume_20d = sum(bar.volume for bar in bars[-volume_window:]) / volume_window
        if avg_volume_20d > 0:
            values["volume_vs_20d_avg_ratio"] = bars[-1].volume / avg_volume_20d

    histogram_values = macd(bars).histogram
    if histogram_values and histogram_values[-1] is not None:
        values["macd_histogram"] = histogram_values[-1]

    kd_result = kd(bars)
    if kd_result.k and kd_result.k[-1] is not None:
        values["kd_k"] = kd_result.k[-1]
    if kd_result.d and kd_result.d[-1] is not None:
        values["kd_d"] = kd_result.d[-1]

    return values


_TECHNICAL_PRICE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "high",
    "low",
    "close",
    "volume",
)


def _bars_from_frame(group: pd.DataFrame) -> tuple[PriceBar, ...]:
    """One symbol's bars, ascending by date, from its slice of the price frame: the same rule as
    `_bars_for_symbol_rows` (a row needs a positive close and a numeric high and low; a missing
    volume is 0)."""
    ordered = group.sort_values("trade_date", kind="stable")
    keep = cast(
        "pd.Series",
        ordered["close"].notna()
        & (ordered["close"] > 0)
        & ordered["high"].notna()
        & ordered["low"].notna(),
    )
    valid = cast("pd.DataFrame", ordered.loc[keep])
    highs = cast("list[float]", valid["high"].tolist())
    lows = cast("list[float]", valid["low"].tolist())
    closes = cast("list[float]", valid["close"].tolist())
    volumes = cast("list[float]", cast("pd.Series", valid["volume"]).fillna(0).tolist())
    return tuple(
        PriceBar(high=float(h), low=float(low), close=float(c), volume=int(v))
        for h, low, c, v in zip(highs, lows, closes, volumes, strict=True)
    )


def _latest_technical_indicators_by_symbol(
    store: ParquetStore, market: str
) -> dict[str, dict[str, float]]:
    """Every scoreable symbol's latest technical-indicator readings, from the 260-day `price_eod`
    window. Read as one columnar frame, with the warrants (about 90% of the rows) dropped inside
    DuckDB: the same read as row dicts for the gated universe was 470-680K dicts, 0.5-0.8 GB, per
    request. The caller caches the result per data day (`_bulk_by_day`), so concurrent requests
    share one read; each filters the symbols it wants (the gated universe) from it."""
    as_of = _latest_price_partition_date(store, market)
    if as_of is None:
        return {}
    calendar = TradingDayCalendar()
    window_start = calendar.shift_business_days(as_of, -(_TECHNICAL_INDICATOR_LOOKBACK_DAYS - 1))
    window_dates = calendar.business_days_between(window_start, as_of)
    frame = store.read_range_frame(
        _PRICE_EOD_DATASET,
        market,
        window_dates,
        columns=_TECHNICAL_PRICE_COLUMNS,
        where=scoreable_symbol_sql(Market(market)),
    )
    by_symbol: dict[str, dict[str, float]] = {}
    for symbol, group in frame.groupby("symbol", sort=False):
        values = _technical_indicator_values(_bars_from_frame(group))
        if values:
            by_symbol[str(symbol)] = values
    return by_symbol


# One result per (kind, market), valid for the data day it was computed for. The inputs change once
# a day (a new partition lands), so a request after the first reuses it; the first of a burst
# computes it and the others wait for that one, instead of each reading the window itself.
_BULK_CACHE: dict[tuple[str, str], tuple[date_, dict[str, dict[str, float]]]] = {}
_BULK_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}


async def _bulk_by_day(
    kind: str,
    market: str,
    as_of: date_ | None,
    compute: Callable[[], dict[str, dict[str, float]]],
) -> dict[str, dict[str, float]]:
    if as_of is None:
        return {}
    key = (kind, market)
    lock = _BULK_LOCKS.setdefault((id(asyncio.get_running_loop()), kind, market), asyncio.Lock())
    async with lock:
        cached = _BULK_CACHE.get(key)
        if cached is not None and cached[0] == as_of:
            return cached[1]
        value = await anyio.to_thread.run_sync(compute)
        _BULK_CACHE[key] = (as_of, value)
        return value


# What `_bulk_by_day` needs for each kind: the data day to key on, and the read itself.
_BULK_SOURCES: dict[
    str,
    tuple[
        Callable[[ParquetStore, str], date_ | None],
        Callable[[ParquetStore, str], dict[str, dict[str, float]]],
    ],
] = {
    "fundamentals": (_fundamentals_as_of, _latest_fundamentals_by_symbol),
    "technical": (_latest_price_partition_date, _latest_technical_indicators_by_symbol),
}


async def refresh_cached_bulk_reads(store: ParquetStore) -> int:
    """Recompute, in the background, the bulk reads this process has already used whose data day
    has moved on, so the first screener request after an ingest finds them ready instead of
    reading the window itself. A kind nobody has asked for is left alone (warming it would hold
    memory for a read no request wants). Returns how many were recomputed."""
    refreshed = 0
    for kind, market in list(_BULK_CACHE):
        source = _BULK_SOURCES.get(kind)
        if source is None:
            continue
        as_of_of, read = source
        as_of = await anyio.to_thread.run_sync(as_of_of, store, market)
        cached = _BULK_CACHE.get((kind, market))
        if as_of is None or (cached is not None and cached[0] == as_of):
            continue
        await _bulk_by_day(kind, market, as_of, functools.partial(read, store, market))
        refreshed += 1
    return refreshed


async def _matched_symbols_for_bulk_source(  # noqa: PLR0913, PLR0917 — one arg per real input
    condition: ScreenerConditionInput,
    field: FactorField,
    gated_universe: set[str],
    cache: dict[str, dict[str, dict[str, float]] | None],
    cache_key: str,
    loader: Callable[[], Awaitable[dict[str, dict[str, float]]]],
) -> set[str]:
    """Shared lazy-cache-then-filter shape for every "one whole-market read, reused across every
    condition referencing it" numeric source (`valuation_ratios`/fundamentals-batch/technical-
    indicator-batch) — pulled out of `_setup_tw`'s loop so adding a fourth bulk source does not
    keep growing that function's own statement count linearly. `loader` is always an
    `anyio.to_thread.run_sync(...)` call at every call site — a real coroutine, not a sync
    callable — so this never needs to branch on the return type."""
    if cache.get(cache_key) is None:
        cache[cache_key] = await loader()
    by_symbol = cache[cache_key] or {}
    return {
        symbol
        for symbol in gated_universe
        if _condition_holds(
            by_symbol.get(symbol, {}).get(field.source_field), condition.op, condition.value
        )
    }


async def _setup_tw(  # noqa: PLR0913 — one arg per real input this stage genuinely reads
    *,
    conditions: tuple[ScreenerConditionInput, ...],
    match: Literal["AND", "OR"],
    gated_universe: set[str],
    tag_reader: ParquetMarketDataReader,
    session_factory: async_sessionmaker[AsyncSession],
    parquet_store: ParquetStore,
) -> tuple[dict[str, list[str]], list[str]]:
    """Stage ⓑ for TW: boolean tag conditions read the flag partition; percentile-numeric
    conditions filter the batch-computed score cache; raw-valuation/fundamentals/technical-
    indicator conditions each read their own whole-market partition directly (lazily, once per
    request, via `_matched_symbols_for_bulk_source`'s shared cache). Returns `(matches_by_symbol,
    degraded_notes)`."""
    catalog = get_factor_catalog(Market.TW.value)
    numeric_by_key = {field.key: field for field in catalog.fields}
    known_tags = tag_keys()

    per_condition: list[set[str]] = []
    labels: list[str] = []
    degraded: list[str] = []

    scores_cache: tuple[ScoreResult, ...] | None = None
    bulk_cache: dict[str, dict[str, dict[str, float]] | None] = {}

    for condition in conditions:
        field = numeric_by_key.get(condition.factor)
        if condition.factor in known_tags:
            partition_date = _latest_tw_flag_date(tag_reader)
            if partition_date is None:
                per_condition.append(set())
                labels.append(condition.factor)
                continue
            rows = tag_reader.read_partition(
                SCREENER_FLAGS_DATASET, Market.TW.value, partition_date
            )
            by_tag, _universe = _flag_symbols_by_tag(rows)
            per_condition.append(by_tag.get(condition.factor, set()) & gated_universe)
            labels.append(condition.factor)
        elif field is not None and field.source_field in _VALUATION_RATIO_SOURCE_FIELDS:
            per_condition.append(
                await _matched_symbols_for_bulk_source(
                    condition,
                    field,
                    gated_universe,
                    bulk_cache,
                    "valuation",
                    lambda: anyio.to_thread.run_sync(
                        _latest_valuation_ratios_by_symbol, parquet_store, Market.TW.value
                    ),
                )
            )
            labels.append(condition.factor)
        elif field is not None and field.source_field in _FUNDAMENTALS_BATCH_SOURCE_FIELDS:
            per_condition.append(
                await _matched_symbols_for_bulk_source(
                    condition,
                    field,
                    gated_universe,
                    bulk_cache,
                    "fundamentals",
                    lambda: _bulk_by_day(
                        "fundamentals",
                        Market.TW.value,
                        _fundamentals_as_of(parquet_store, Market.TW.value),
                        lambda: _latest_fundamentals_by_symbol(parquet_store, Market.TW.value),
                    ),
                )
            )
            labels.append(condition.factor)
        elif field is not None and field.source_field in _TECHNICAL_INDICATOR_SOURCE_FIELDS:
            per_condition.append(
                await _matched_symbols_for_bulk_source(
                    condition,
                    field,
                    gated_universe,
                    bulk_cache,
                    "technical",
                    lambda: _bulk_by_day(
                        "technical",
                        Market.TW.value,
                        _latest_price_partition_date(parquet_store, Market.TW.value),
                        lambda: _latest_technical_indicators_by_symbol(
                            parquet_store, Market.TW.value
                        ),
                    ),
                )
            )
            labels.append(condition.factor)
        elif field is not None:
            if scores_cache is None:
                async with session_factory() as session:
                    scores_cache = await get_all_scores_for_date(
                        session, market=Market.TW, profile=ScoreProfile.SWING
                    )
            source_field = field.source_field
            matched = {
                result.symbol
                for result in scores_cache
                if result.symbol in gated_universe
                and _condition_holds(
                    _score_field_value(result, source_field), condition.op, condition.value
                )
            }
            per_condition.append(matched)
            labels.append(condition.factor)
        else:
            degraded.append(f"unknown TW factor key: {condition.factor}")

    matches_by_symbol = _combine(per_condition, labels, match)
    return matches_by_symbol, degraded


def _latest_tw_flag_date(reader: ParquetMarketDataReader) -> date_ | None:
    market_dir = reader.root / f"dataset={SCREENER_FLAGS_DATASET}" / "market=TW"
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
        if reader.has_partition(SCREENER_FLAGS_DATASET, Market.TW.value, day):
            candidates.append(day)
    return max(candidates) if candidates else None


def _flag_symbols_by_tag(rows: list[dict[str, object]]) -> tuple[dict[str, set[str]], set[str]]:
    by_tag: dict[str, set[str]] = {}
    universe: set[str] = set()
    for row in rows:
        symbol = row.get("symbol")
        tag = row.get("tag")
        if isinstance(symbol, str) and isinstance(tag, str) and symbol and tag:
            by_tag.setdefault(tag, set()).add(symbol)
            universe.add(symbol)
    return by_tag, universe


def _combine(
    per_condition: list[set[str]], labels: list[str], match: Literal["AND", "OR"]
) -> dict[str, list[str]]:
    """AND: intersect every condition's symbol set, matched_conditions = all labels. OR: union,
    matched_conditions = the labels each symbol actually satisfied."""
    if not per_condition:
        return {}
    if match == "AND":
        combined = set(per_condition[0])
        for symbols in per_condition[1:]:
            combined &= symbols
        return {symbol: list(labels) for symbol in combined}
    result: dict[str, list[str]] = {}
    for label, symbols in zip(labels, per_condition, strict=True):
        for symbol in symbols:
            result.setdefault(symbol, []).append(label)
    return result


def _setup_etf(
    *,
    conditions: tuple[ScreenerConditionInput, ...],
    match: Literal["AND", "OR"],
    gated_universe: dict[str, dict[str, object]],
) -> tuple[dict[str, list[str]], list[str]]:
    catalog = get_factor_catalog("ETF")
    numeric_by_key = {field.key: field for field in catalog.fields}
    degraded: list[str] = []
    per_condition: list[set[str]] = []
    labels: list[str] = []

    for condition in conditions:
        field = numeric_by_key.get(condition.factor)
        if field is None:
            degraded.append(f"unknown ETF factor key: {condition.factor}")
            continue
        if condition.op is ScreenerOperator.EQ:
            matched = {
                symbol
                for symbol, row in gated_universe.items()
                if _string_condition_holds(
                    _as_str(row.get(field.source_field)), condition.op, condition.value
                )
            }
        else:
            matched = {
                symbol
                for symbol, row in gated_universe.items()
                if _condition_holds(
                    _as_float(row.get(field.source_field)), condition.op, condition.value
                )
            }
        per_condition.append(matched)
        labels.append(condition.factor)

    return _combine(per_condition, labels, match), degraded


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


# Yahoo quote fields promoted onto every US `ScreenerSurvivor.display` — regardless of which
# factor(s) the caller actually queried on, so the results table can render price/sector/market
# cap/PE without a second per-symbol lookup. These are exactly the fields `_quote_to_hit` already
# normalizes into `ScreenerHit.matched_fields`/named attrs; this is a display projection of data
# Yahoo's screener response already returned for the hit, never an extra fetch.
_US_DISPLAY_FIELDS: tuple[str, ...] = (
    "regularMarketPrice",
    "regularMarketChangePercent",
    "regularMarketVolume",
    "marketCap",
    "sector",
    "peratio.lasttwelvemonths",
    "pctheldinst",
    "short_percentage_of_float.value",
)


async def _setup_us(
    conditions: tuple[ScreenerConditionInput, ...],
    match: Literal["AND", "OR"],
    size: int,
) -> tuple[dict[str, list[str]], dict[str, dict[str, float | str | bool | None]], list[str]]:
    """Stage ⓑ for US: builds one `yf.EquityQuery` AND/OR tree via the existing `dataaccess/
    screener.py` seam. Unlike TW/ETF, there is no separate gated-universe pre-filter — Yahoo's
    screener query itself is the whole-universe scan (that's the point of a hosted screener
    endpoint), so gates are folded in as extra AND'd conditions rather than a client-side
    universe set (kept out of this pass's scope — see `RunScreenerOutput` callers; a follow-up
    can fold `gates.py`'s `min_price`/`min_market_cap` into the yfinance query directly)."""
    catalog = get_factor_catalog(Market.US.value)
    numeric_by_key = {field.key: field for field in catalog.fields}
    degraded: list[str] = []
    screener_conditions: list[ScreenerCondition] = []
    labels: list[str] = []

    for condition in conditions:
        field = numeric_by_key.get(condition.factor)
        if field is None:
            degraded.append(f"unknown US factor key: {condition.factor}")
            continue
        screener_conditions.append(
            ScreenerCondition(
                field=field.source_field,
                op=condition.op,
                value=condition.value,
                value2=condition.value2,
            )
        )
        labels.append(condition.factor)

    if not screener_conditions:
        return {}, {}, degraded

    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=tuple(screener_conditions),
        match=match,
        size=size,
    )
    result = await run_equity_screener(query)
    matches = {hit.symbol: list(labels) for hit in result.hits}
    display = {
        hit.symbol: {
            field: hit.matched_fields.get(field)
            for field in _US_DISPLAY_FIELDS
            if field in hit.matched_fields
        }
        for hit in result.hits
    }
    return matches, display, degraded


async def run_screener(
    typed: RunScreenerInput,
    *,
    parquet_store: ParquetStore,
    tag_reader: ParquetMarketDataReader,
    session_factory: async_sessionmaker[AsyncSession],
) -> RunScreenerOutput:
    """The funnel entry point: resolve gates, apply stage ⓐ, evaluate stage ⓑ conditions, return
    survivors. `count_only` skips assembling `ScreenerSurvivor` rows once the count is known."""
    gates = resolve_gates(typed.market, typed.gate_overrides)
    degraded: list[str] = []
    display_by_symbol: dict[str, dict[str, float | str | bool | None]] = {}

    if typed.market == Market.TW.value:
        gated_universe, _as_of = await anyio.to_thread.run_sync(
            _gated_tw_universe, parquet_store, gates
        )
        universe_size = len(gated_universe)
        matches, tw_degraded = await _setup_tw(
            conditions=typed.conditions,
            match=typed.match,
            gated_universe=gated_universe,
            tag_reader=tag_reader,
            session_factory=session_factory,
            parquet_store=parquet_store,
        )
        degraded.extend(tw_degraded)
    elif typed.market == "ETF":
        gated_etf, _as_of = await anyio.to_thread.run_sync(
            _gated_etf_universe, parquet_store, gates
        )
        universe_size = len(gated_etf)
        matches, etf_degraded = _setup_etf(
            conditions=typed.conditions, match=typed.match, gated_universe=gated_etf
        )
        degraded.extend(etf_degraded)
    else:  # US
        universe_size = 0  # unknown without a live query — yfinance's screener IS the universe
        matches, display_by_symbol, us_degraded = await _setup_us(
            typed.conditions, typed.match, typed.size
        )
        degraded.extend(us_degraded)

    setup_size = len(matches)
    detail = (
        f"{setup_size} symbol(s) matched under {typed.match} "
        f"({len(typed.conditions)} condition(s), market={typed.market})"
    )
    if typed.count_only:
        return RunScreenerOutput(
            market=typed.market,
            funnel=FunnelCounts(universe=universe_size, gated=universe_size, setup=setup_size),
            survivors=(),
            detail=detail,
            degraded=tuple(degraded),
        )

    survivors = tuple(
        ScreenerSurvivor(
            symbol=symbol,
            matched_conditions=tuple(labels),
            display=display_by_symbol.get(symbol, {}),
        )
        for symbol, labels in sorted(matches.items())
    )[: typed.size]
    return RunScreenerOutput(
        market=typed.market,
        funnel=FunnelCounts(universe=universe_size, gated=universe_size, setup=setup_size),
        survivors=survivors,
        detail=detail,
        degraded=tuple(degraded),
    )


__all__ = [
    "FunnelCounts",
    "RunScreenerInput",
    "RunScreenerOutput",
    "ScreenerConditionInput",
    "ScreenerSurvivor",
    "refresh_cached_bulk_reads",
    "run_screener",
]
