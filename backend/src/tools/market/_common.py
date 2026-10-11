"""Helpers and models shared by more than one market tool module."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from datetime import date as date_
from enum import StrEnum

from analytics.scoring_batch import US_PRICE_EOD_DATASET, US_SECTOR_CLASS_DATASET
from analytics.symbol_universe import is_scoreable_symbol
from ingest.jobs import (
    ETF_AUM_DATASET,
    ETF_NAV_DATASET,
    MARKET_VALUE_JOB,
    PRICE_EOD_JOB,
    STOCK_INFO_JOB,
)
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from ingest.us_universe import US_SYMBOL_UNIVERSE_DATASET
from schemas.capabilities import EtfAumRow, MarketValueRow, SectorClassification, StockInfoRow
from schemas.enums import Market

# The untyped edges every `cast(...)` below narrows a `SourceResult.payload` value from — PEP-695
# local aliases instead of repeating the same cast-target strings at every call site.
type RowList = list[dict[str, object]]


type UntypedDict = dict[str, object]


class HoldingStatus(StrEnum):
    """The derived per-holding classification layered on top of FinMind's raw buy/sell change
    row — never stored by FinMind itself, computed here from (buy, sell, prior-day weight)."""

    NEW = "新增"
    REMOVED = "刪除"
    INCREASED = "加碼"
    DECREASED = "減碼"
    NO_PRIOR_DATA = "no_prior_data"  # e.g. the very first ingested day for this ETF


def classify_holding_status(
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
    # `buy > sell` (not `buy > 0`) because callers aggregating across multiple ETFs (daily-signal)
    # can have both nonzero at once — one ETF buying while another sells the same stock — where a
    # single ETF's own change row never does (exactly one of buy/sell is nonzero, per above).
    if buy > sell:
        return HoldingStatus.INCREASED
    return HoldingStatus.DECREASED


def prior_report_date(dates: list[date_], target: date_) -> date_ | None:
    """The date immediately before `target` within the fetched window — `None` if `target` is
    the earliest date the window returned (treated as "no prior data", never guessed)."""
    idx = dates.index(target)
    return dates[idx - 1] if idx > 0 else None


def price_eod_dataset_for(market: str) -> str:
    """`(price_eod dataset name)` for `market` — TW's whole-market breadth/momentum/heatmap
    tools (`GetAdvanceDeclineTool` and siblings) all hardcoded `PRICE_EOD_JOB.dataset`
    ("TaiwanStockPrice") until this function existed; passing `market=US` into any of them
    unmodified would look up that TW dataset name under `market=US` and find nothing — US's
    whole-market OHLCV lives under a different dataset name entirely (`US_PRICE_EOD_DATASET`,
    `ingest/us_jobs.py::job_us_price_eod`). Mirrors `analytics/scoring_batch.py::
    _price_eod_dataset`'s identical branch — kept as a separate function here (not imported)
    because that one takes a `Market` enum member, this one the `.value` string every caller in
    this module already has in hand."""
    return US_PRICE_EOD_DATASET if market == Market.US.value else PRICE_EOD_JOB.dataset


def industry_by_symbol_for(store: ParquetStore, market: str) -> dict[str, str]:
    """`(symbol -> group label)` for `market`, the roster join every sector-heatmap/
    stock-heatmap/sector-constituents tool needs. TW groups by `StockInfoRow.industry_category`
    (FinMind's Chinese industry taxonomy, from `STOCK_INFO_JOB`). US has no equivalent field —
    `us_sector_class`'s `SectorClassification` rows carry `sector` (yfinance's broad ~11-bucket
    GICS-ish taxonomy, e.g. "Technology") and `industry` (a much finer taxonomy, e.g. "Consumer
    Electronics") separately. Groups US by `sector`, not `industry`: the ~1,000-1,500-symbol US
    universe split across yfinance's fine `industry` taxonomy would produce a heatmap with
    hundreds of near-empty tiles, whereas `sector`'s ~11 buckets read the same as TW's own
    industry-heatmap tile count. Returns `{}` (never raises) when no roster/sector-class
    partition has landed yet — callers already treat an empty join map as "nothing to group",
    the same as a missing roster does for TW."""
    if market == Market.US.value:
        roster_date = latest_dataset_date(store, US_SECTOR_CLASS_DATASET, market)
        if roster_date is None:
            return {}
        raw = store.read_partition(US_SECTOR_CLASS_DATASET, market, roster_date)
        sector_rows = rows_from_store(SectorClassification, raw)
        return {row.symbol: row.sector for row in sector_rows if row.sector}
    roster_date = latest_dataset_date(store, STOCK_INFO_JOB.dataset, market)
    if roster_date is None:
        return {}
    raw = store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
    stock_info_rows = rows_from_store(StockInfoRow, raw)
    return {row.symbol: row.industry_category for row in stock_info_rows if row.industry_category}


def names_by_symbol_for(store: ParquetStore, market: str) -> dict[str, str]:
    """`(symbol -> display name)` for `market` — `get_price_momentum`'s roster-name join. TW
    reads `STOCK_INFO_JOB`'s `name` field; US has no `StockInfoRow`-shaped roster, but
    `US_SYMBOL_UNIVERSE_DATASET` (`ingest/us_universe.py::job_us_symbol_universe`) writes
    `{"symbol", "name", "exchange"}` rows — that dataset's own read-back helper
    (`latest_us_symbols`) discards `name` (it only needs the symbol list for the price/
    fundamentals ingest fan-out), so this reads the raw partition directly rather than adding a
    second dataset or a second US-roster read path."""
    if market == Market.US.value:
        roster_date = latest_dataset_date(store, US_SYMBOL_UNIVERSE_DATASET, market)
        if roster_date is None:
            return {}
        raw = store.read_partition(US_SYMBOL_UNIVERSE_DATASET, market, roster_date)
        return {
            str(row["symbol"]): str(row["name"])
            for row in raw
            if row.get("symbol") and row.get("name")
        }
    roster_date = latest_dataset_date(store, STOCK_INFO_JOB.dataset, market)
    if roster_date is None:
        return {}
    raw = store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
    rows = rows_from_store(StockInfoRow, raw)
    return {row.symbol: row.name for row in rows}


def real_calendar(store: ParquetStore, market: str) -> TradingDayCalendar:
    """The real trading-day calendar for `market`, built from `PRICE_EOD_JOB`'s own
    already-ingested partition dates — same reasoning and construction as
    `ingest/daemon.py::_build_calendar`, duplicated here rather than imported because `tools/`
    may not import `ingest/` (the DEPENDENCY RULE only allows `tools -> context -> dataaccess ->
    sources`; `ingest` is a sibling ingestion-side module, not a lower layer this one may pull
    from). A plain `TradingDayCalendar()` (naive Mon-Fri weekday math) previously used at every
    call site here would miscount real TW holidays as trading days, corrupting the "prior
    trading day" every close-to-close calculation in this module depends on — the same class of
    bug fixed in the ingest daemon's own calendar construction. Resolves the price_eod dataset
    name via `price_eod_dataset_for` — a no-op for every existing TW caller (that function
    returns the identical `PRICE_EOD_JOB.dataset` string for `market="TW"`), but correctly reads
    `us_price_eod`'s own partition dates for `market="US"` instead of a TW dataset name that has
    no `market=US` partitions at all."""
    known_dates = store.list_partition_dates(price_eod_dataset_for(market), market)
    if not known_dates:
        return TradingDayCalendar()
    return calendar_from_known_dates(known_dates)


def closes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every real equity/ETF symbol's closing price on `trade_date`, read from the whole-market
    price_eod partition the daily ingest job already wrote (`price_eod_dataset_for` resolves
    `TaiwanStockPrice` for TW / `us_price_eod` for US) — the same derived-store read
    `GetEtfMoneyFlowTool` uses, not a per-symbol live fetch (`_active_etf_holding_value`'s own
    docstring explains why this beats trusting FinMind's own `market_value` field).

    Filtered through `analytics.symbol_universe.is_scoreable_symbol`, with `market` passed
    through explicitly: TW's raw partition is dominated by warrant/derivative codes (~17K/day
    vs. ~2.65K real equities/ETFs — see that module's docstring for the measured split), which
    would otherwise inflate every breadth/contribution/heatmap calculation built from this map
    with thousands of warrant rows that don't belong in "how many real stocks moved today". US
    has no such warrant pollution (`ingest/us_universe.py`'s screener-based discovery already
    excludes it) — `is_scoreable_symbol` unconditionally passes every US symbol, but the `market`
    arg must still be threaded through: omitting it defaults to TW's length/prefix code-shape
    rule, which is simply wrong for US tickers (see that function's own docstring)."""
    market_enum = Market(market)
    raw_bars = store.read_partition(price_eod_dataset_for(market), market, trade_date)
    closes: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        close = row.get("close")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol, market_enum)
            and isinstance(close, (int, float))
        ):
            closes[symbol] = float(close)
    return closes


def shares_outstanding_by_symbol(
    store: ParquetStore, market: str, trade_date: date_
) -> dict[str, float]:
    """Every symbol's shares-outstanding on `trade_date`, derived as `MarketValueRow.market_value
    ÷ that day's close` — market cap is shares-outstanding x price by definition, so this
    recovers a real share count (to rounding) from two already-ingested whole-market datasets
    (`MARKET_VALUE_JOB` + `PRICE_EOD_JOB`), never a live per-symbol fetch. Read from the same
    Parquet partition tree `closes_by_symbol` already reads for the analogous close-price map.
    Skips a symbol entirely (rather than a fabricated `0`/`None` sentinel in the map) when either
    its market-value row or its close is missing on this date."""
    closes = closes_by_symbol(store, market, trade_date)
    if not store.has_partition(MARKET_VALUE_JOB.dataset, market, trade_date):
        return {}
    raw_values = store.read_partition(MARKET_VALUE_JOB.dataset, market, trade_date)
    shares_outstanding: dict[str, float] = {}
    for row in rows_from_store(MarketValueRow, raw_values):
        close = closes.get(row.symbol)
        if close:
            shares_outstanding[row.symbol] = row.market_value / close
    return shares_outstanding


def ingested_dates(
    store: ParquetStore, dataset: str, market: str, *, on_or_before: date_ | None = None
) -> list[date_]:
    """Every date `dataset`+`market` has a real, non-empty partition for — shared by every
    "latest real date" helper below (`latest_dataset_date`, `_latest_ingested_date_on_or_
    before`, `_two_latest_ingested_dates_on_or_before`) so the real-partition check exists
    exactly once. Delegates to `store.list_partition_dates`, NOT `store.has_partition` (that
    only confirms a manifest exists, not that the partition has real rows — confirmed live this
    session: a stale zero-row placeholder partition made this return a "latest" date whose
    `read_partition` then silently came back empty, blanking every ETF's 費用率 column). `on_or_
    before`, when given, drops any later date before returning — a slower-cadence dataset like
    `ACTIVE_ETF_HOLDING_JOB` can lag the `PRICE_EOD_JOB`-derived trading-day calendar by more
    than one day, so its latest real partition may sit entirely outside a short requested
    window."""
    candidates = list(store.list_partition_dates(dataset, market))
    if on_or_before is not None:
        candidates = [day for day in candidates if day <= on_or_before]
    return candidates


def latest_ingested_date_on_or_before(
    store: ParquetStore, dataset: str, market: str, on_or_before: date_
) -> date_ | None:
    """The latest date `dataset`+`market` has an actually-written partition for, at or before
    `on_or_before`. Reading a not-yet-ingested partition for the calendar's own last business day
    would otherwise silently return empty rows instead of falling back to the most recent real
    one. `None` (never a guessed date) when nothing at or before `on_or_before` has landed."""
    candidates = ingested_dates(store, dataset, market, on_or_before=on_or_before)
    return max(candidates) if candidates else None


def latest_dataset_date(store: ParquetStore, dataset: str, market: str) -> date_ | None:
    """The newest date `dataset`+`market` has an actually-written partition for, via the shared
    `ingested_dates` scan (mirrors `analytics.screener.store._latest_partition_date`'s own
    scan, generalized to any whole-market dataset rather than just `screener_flags`). Never
    assumes "today" has landed; the daily ingest job may not have run yet, so "latest" always
    means "latest actually written", never a naive `date.today()`."""
    candidates = ingested_dates(store, dataset, market)
    return max(candidates) if candidates else None


def two_day_window(
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
    trade_date = requested or latest_dataset_date(store, dataset, market)
    if trade_date is None or not store.has_partition(dataset, market, trade_date):
        return None
    prior_date = calendar.shift_business_days(trade_date, -1)
    if not store.has_partition(dataset, market, prior_date):
        return None
    return trade_date, prior_date


def unavailable_reason_for_window(dataset_label: str, market: str, trade_date: date_ | None) -> str:
    """The shared "no partition (yet)" message every two-day-window tool in this module
    surfaces when `two_day_window` returns `None` — one wording, not one per tool."""
    when = f" on {trade_date.isoformat()}" if trade_date else ""
    return (
        f"No ingested {dataset_label} partition (and/or its prior trading day) found for "
        f"{market}{when} — the whole-market ingest job may not have run yet for this date."
    )


ETF_AUM_CAVEAT = (
    "資產規模 (AUM) is Yahoo's own totalAssets figure for this fund — refreshed weekly, not a "
    "live figure; may be missing for a recently-listed or thinly-covered fund."
)


def etf_nav_names(store: ParquetStore, market: str) -> dict[str, str]:
    """Every ETF symbol's fund name from the latest `etf_nav` partition — `EtfNavRow.fund_name`
    is a required field on any row that validated, so a symbol present here always has a real
    name, never a placeholder."""
    nav_date = latest_dataset_date(store, ETF_NAV_DATASET, market)
    if nav_date is None:
        return {}
    raw_nav = store.read_partition(ETF_NAV_DATASET, market, nav_date)
    names: dict[str, str] = {}
    for row in raw_nav:
        symbol = row.get("symbol")
        fund_name = row.get("fund_name")
        if isinstance(symbol, str) and isinstance(fund_name, str) and fund_name:
            names[symbol] = fund_name
    return names


def etf_aum_by_symbol(store: ParquetStore, market: str) -> dict[str, float]:
    aum_date = latest_dataset_date(store, ETF_AUM_DATASET, market)
    if aum_date is None:
        return {}
    raw_aum = store.read_partition(ETF_AUM_DATASET, market, aum_date)
    return {
        row.symbol: row.aum for row in rows_from_store(EtfAumRow, raw_aum) if row.aum is not None
    }
