"""Symbol reference and list-row tools: roster lookup, directory search, price history
and the composed list-row quotes."""

from __future__ import annotations

from datetime import date as date_
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analytics.symbol_universe import SymbolType, classify_symbol_type, is_scoreable_symbol
from ingest.jobs import STOCK_INFO_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from ingest.us_universe import US_SYMBOL_UNIVERSE_DATASET
from schemas.capabilities import EODBar, StockInfoRow, SymbolDirectorySearchRow
from schemas.enums import DataClass, Market
from tools.base import BaseTool, SyncStoreTool, ToolContext, parse_args
from tools.market._common import (
    industry_by_symbol_for,
    latest_dataset_date,
    names_by_symbol_for,
    price_eod_dataset_for,
    real_calendar,
    unavailable_reason_for_window,
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


class GetSymbolInfoTool(SyncStoreTool):
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
    # No card: this is a name/industry lookup the model reads, and the raw key/value card it used to
    # produce (market, roster date, "data available") told the reader nothing.
    card_type: str | None = None

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetSymbolInfoInput, args)
        market = typed.market.value
        wanted = set(typed.symbols)
        if market == Market.US.value:
            return self._run_us(market, wanted)
        roster_date = latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetSymbolInfoOutput(
                market=market,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, None),
            )
        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = rows_from_store(StockInfoRow, raw_roster)
        rows = tuple(row for row in roster if row.symbol in wanted)
        return GetSymbolInfoOutput(market=market, roster_date=roster_date, rows=rows)

    def _run_us(self, market: str, wanted: set[str]) -> GetSymbolInfoOutput:
        """US has no `StockInfoRow`-shaped roster — assembles the equivalent from
        `us_symbol_universe` (name) joined with `us_sector_class` (industry, via
        `industry_by_symbol_for`'s broad `sector` grouping); `security_type` is always
        "equity" since the US universe is screener-discovered common stock, never a warrant
        roster. `roster_date` is `names_by_symbol_for`'s own underlying partition date."""
        roster_date = latest_dataset_date(self._store, US_SYMBOL_UNIVERSE_DATASET, market)
        if roster_date is None:
            return GetSymbolInfoOutput(
                market=market,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, None),
            )
        names_by_symbol = names_by_symbol_for(self._store, market)
        industry_by_symbol = industry_by_symbol_for(self._store, market)
        rows = tuple(
            StockInfoRow(
                symbol=symbol,
                market=Market.US,
                name=name,
                industry_category=industry_by_symbol.get(symbol, ""),
                security_type="equity",
                as_of=roster_date,
            )
            for symbol, name in names_by_symbol.items()
            if symbol in wanted
        )
        return GetSymbolInfoOutput(market=market, roster_date=roster_date, rows=rows)


_SYMBOL_DIRECTORY_SEARCH_MAX_LIMIT = 20


class GetSymbolDirectorySearchInput(BaseModel):
    """Free-text query against the same whole-market roster `GetSymbolInfoTool` reads — this is
    the TW counterpart to `analyst_tools.GetSymbolSearchTool` (`get_symbol_search`, yfinance),
    which explicitly refuses `market=TW` since it has no TW roster to search. `limit` is bounded
    the same way `symbols` is bounded elsewhere in this module — a type-ahead call, not a
    whole-roster dump."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1)
    market: Market = Market.TW
    limit: int = Field(default=_SYMBOL_DIRECTORY_SEARCH_MAX_LIMIT, ge=1, le=50)


class GetSymbolDirectorySearchOutput(BaseModel):
    """Same "absent, never fabricated" contract as `GetSymbolInfoOutput` — an empty `rows` means
    no roster match, not a placeholder result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    roster_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    rows: tuple[SymbolDirectorySearchRow, ...] = Field(default_factory=tuple)


class GetSymbolDirectorySearchTool(SyncStoreTool):
    """`get_symbol_directory_search` — free-text symbol/name type-ahead over the ingested
    `STOCK_INFO_JOB` roster (stocks + ETFs together, warrants excluded via `is_scoreable_symbol`).
    Ranks exact-prefix-on-symbol first, then prefix-on-name, then substring-anywhere — a plain
    Python scan over ~2.7K rows per call, not a search index, since the whole roster already sits
    in memory for the duration of one request and re-scanning it per keystroke is still
    sub-millisecond at this size. Backs the TW leg of the `/search` route; the yfinance-backed
    `get_symbol_search` remains the non-TW leg (see its own class docstring for why the two are
    never merged into one call)."""

    name = "get_symbol_directory_search"
    description = "Search the TW stock/ETF roster by free-text company name or ticker (type-ahead)."
    input_model: type[BaseModel] = GetSymbolDirectorySearchInput
    output_model: type[BaseModel] = GetSymbolDirectorySearchOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "symbol_search"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetSymbolDirectorySearchInput, args)
        market = typed.market.value
        roster_date = latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetSymbolDirectorySearchOutput(
                market=market,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, None),
            )
        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = rows_from_store(StockInfoRow, raw_roster)
        rows = _rank_symbol_directory_matches(roster, typed.query, typed.market, typed.limit)
        return GetSymbolDirectorySearchOutput(market=market, roster_date=roster_date, rows=rows)


def _rank_symbol_directory_matches(
    roster: list[StockInfoRow], query: str, market: Market, limit: int
) -> tuple[SymbolDirectorySearchRow, ...]:
    """Rank tier: 0 = symbol starts with `query`, 1 = name starts with `query`, 2 = substring
    match anywhere in symbol/name. Ties within a tier keep the roster's own order (alphabetical
    by symbol, per `STOCK_INFO_JOB`), so results are stable across repeated calls. The roster
    itself carries duplicate `(symbol, name)` rows under more than one `industry_category`
    (confirmed live, e.g. `2330`/`2454` both list under 半導體業 and 電子工業) — the first-seen
    row per symbol wins, since a search result is "this symbol", not "this symbol per category"."""
    needle = query.strip().casefold()
    if not needle:
        return ()
    ranked: list[tuple[int, SymbolDirectorySearchRow]] = []
    seen_symbols: set[str] = set()
    for row in roster:
        if row.symbol in seen_symbols or not is_scoreable_symbol(row.symbol, market):
            continue  # excludes warrants — same filter every other whole-roster reader applies
        symbol_cf = row.symbol.casefold()
        name_cf = row.name.casefold()
        if symbol_cf.startswith(needle):
            tier = 0
        elif name_cf.startswith(needle):
            tier = 1
        elif needle in symbol_cf or needle in name_cf:
            tier = 2
        else:
            continue
        seen_symbols.add(row.symbol)
        symbol_type = classify_symbol_type(row.symbol, market)
        kind: Literal["stock", "etf"] = "stock" if symbol_type is SymbolType.STOCK else "etf"
        ranked.append(
            (
                tier,
                SymbolDirectorySearchRow(
                    symbol=row.symbol,
                    name=row.name,
                    instrument_kind=kind,
                    industry_category=row.industry_category or None,
                ),
            )
        )
    ranked.sort(key=lambda pair: pair[0])
    return tuple(row for _, row in ranked[:limit])


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


class GetPriceHistoryTool(SyncStoreTool):
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

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetPriceHistoryInput, args)
        market = typed.market.value
        price_eod_dataset = price_eod_dataset_for(market)
        latest = latest_dataset_date(self._store, price_eod_dataset, market)
        if latest is None:
            return GetPriceHistoryOutput(
                market=market,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("price_eod", market, None),
            )
        calendar = real_calendar(self._store, market)
        start = calendar.shift_business_days(latest, -(typed.days - 1))
        dates = calendar.business_days_between(start, latest)
        # Symbol filter pushed into the DuckDB read itself (`read_range`'s `symbols=`) rather
        # than validating every symbol in the market for this window and discarding almost all
        # of it in Python — this tool's own contract caps `symbols` at 20, so the filtered read
        # is at most 20 symbols x `days` rows, not the whole market's.
        raw_rows = self._store.read_range(price_eod_dataset, market, dates, symbols=typed.symbols)
        rows = tuple(row for row in rows_from_store(EODBar, raw_rows) if row.market.value == market)
        return GetPriceHistoryOutput(market=market, start_date=start, end_date=latest, rows=rows)


class GetSymbolQuotesInput(BaseModel):
    """Same bounds as `get_price_history`: ≤20 symbols, a 2-60 trading-day sparkline window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: tuple[str, ...] = Field(min_length=1, max_length=_MAX_BATCH_SYMBOLS)
    market: Market = Market.TW
    days: int = Field(default=20, ge=2, le=_MAX_PRICE_HISTORY_DAYS)


class SymbolQuoteRow(BaseModel):
    """One list row. `change`/`change_pct` are always the DAILY move (latest close vs. the
    previous trading day's close), whatever `days` the sparkline spans; `None` when fewer than
    two bars exist. `name` is `None` for a symbol absent from the roster, never a placeholder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    industry_category: str | None = None
    trade_date: date_ | None = None
    close: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    sparkline: tuple[float, ...] = Field(default_factory=tuple)


class GetSymbolQuotesOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    end_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    # In request order, one per requested symbol (a symbol with no data still gets a row with
    # null fields, so a list keeps its shape).
    rows: tuple[SymbolQuoteRow, ...] = Field(default_factory=tuple)


def _quote_row(symbol: str, info: StockInfoRow | None, bars: list[EODBar]) -> SymbolQuoteRow:
    bars.sort(key=lambda bar: bar.trade_date)
    last = bars[-1] if bars else None
    prev = bars[-2] if len(bars) >= 2 else None  # noqa: PLR2004 — the previous trading day
    change = change_pct = None
    if last is not None and prev is not None and prev.close != 0:
        change = last.close - prev.close
        change_pct = (last.close / prev.close - 1) * 100
    return SymbolQuoteRow(
        symbol=symbol,
        name=info.name if info else None,
        industry_category=(info.industry_category or None) if info else None,
        trade_date=last.trade_date if last else None,
        close=last.close if last else None,
        prev_close=prev.close if prev else None,
        change=change,
        change_pct=change_pct,
        volume=last.volume if last else None,
        sparkline=tuple(bar.close for bar in bars),
    )


class GetSymbolQuotesTool(BaseTool):
    """`get_symbol_quotes` — the list-row view (name + latest close + daily change + volume +
    sparkline) for up to 20 symbols in one call. Composes `get_symbol_info` and
    `get_price_history` server-side so every list (leaderboard, watchlist, rankings, screener)
    shows the SAME daily change, instead of each client caller merging two reads and picking
    its own %-change window. Web-only like `get_price_history` until a skill lists it."""

    name = "get_symbol_quotes"
    description = (
        "Get name, latest close, daily change and change %, volume, and a closing-price "
        "sparkline for up to 20 stock/ETF symbols in one bounded call."
    )
    input_model: type[BaseModel] = GetSymbolQuotesInput
    output_model: type[BaseModel] = GetSymbolQuotesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, store: ParquetStore) -> None:
        self._info = GetSymbolInfoTool(store)
        self._history = GetPriceHistoryTool(store)

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetSymbolQuotesInput, args)
        info = await self._info.run(
            GetSymbolInfoInput(symbols=typed.symbols, market=typed.market), ctx
        )
        history = await self._history.run(
            GetPriceHistoryInput(symbols=typed.symbols, market=typed.market, days=typed.days), ctx
        )
        assert isinstance(info, GetSymbolInfoOutput)  # noqa: S101 — narrows the BaseTool return
        assert isinstance(history, GetPriceHistoryOutput)  # noqa: S101
        info_by_symbol: dict[str, StockInfoRow] = {}
        for row in info.rows:
            info_by_symbol.setdefault(row.symbol, row)  # the roster repeats a symbol per category
        bars_by_symbol: dict[str, list[EODBar]] = {}
        for bar in history.rows:
            bars_by_symbol.setdefault(bar.symbol, []).append(bar)
        rows = tuple(
            _quote_row(symbol, info_by_symbol.get(symbol), bars_by_symbol.get(symbol, []))
            for symbol in dict.fromkeys(typed.symbols)
        )
        return GetSymbolQuotesOutput(
            market=typed.market.value,
            end_date=history.end_date,
            data_available=history.data_available,
            unavailable_reason=history.unavailable_reason,
            rows=rows,
        )
