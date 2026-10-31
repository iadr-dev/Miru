"""Loading and reshaping the stored datasets into per-symbol series and universes."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date as date_
from typing import cast

import pandas as pd
import structlog
from pandas.api.types import (
    union_categoricals as _union_categoricals,  # pyright: ignore[reportUnknownVariableType]
)

from analytics.coerce import as_float, as_int
from analytics.fundamentals import (
    FundamentalMetrics,
    compute_revenue_yoy,
    parse_market_value_rows,
    parse_month_revenue_rows,
    parse_statement_lines,
)
from analytics.indicators import PriceBar, sma
from analytics.scoring_batch.datasets import MA_WINDOWS as _MA_WINDOWS
from analytics.scoring_batch.datasets import STOCK_INFO_DATASET
from analytics.symbol_universe import SymbolType, classify_symbol_type, scoreable_symbol_mask
from ingest.context import IngestContext
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    AnalystConsensus,
    EtfExpenseInfo,
    EtfNavRow,
    FinancialStatementLine,
    InsiderActivityRow,
    InstitutionalFlowRow,
    InstitutionalHolderRow,
    MarketValueRow,
    MonthRevenueRow,
    SectorClassification,
    StockInfoRow,
)
from schemas.enums import Market

logger = structlog.get_logger(__name__)


def active_roster_symbols(ctx: IngestContext, market_str: str) -> frozenset[str] | None:
    """Every symbol the *latest* `STOCK_INFO_JOB` roster partition actually lists — the same
    "latest real date" lookup `tools/market/_common.py::latest_dataset_date` performs, inlined here
    rather than imported since `analytics/` may not depend on `tools/` (dependency rule, backend/
    CLAUDE.md §3). Returns `None`, not an empty set, when the roster has never been ingested for
    this market — the caller must treat that as "no roster to filter against" and skip filtering,
    never as "the roster is empty" (which would silently drop every symbol from the score
    universe)."""
    partition_dates = ctx.store.list_partition_dates(STOCK_INFO_DATASET, market_str)
    if not partition_dates:
        return None
    roster_date = max(partition_dates)
    raw_roster = ctx.store.read_partition(STOCK_INFO_DATASET, market_str, roster_date)
    field_names = set(StockInfoRow.model_fields)
    roster = [
        StockInfoRow.model_validate({k: v for k, v in row.items() if k in field_names})
        for row in raw_roster
    ]
    return frozenset(row.symbol for row in roster)


def calendar_for(ctx: IngestContext) -> TradingDayCalendar:
    """`ctx.us_calendar` for a `Market.US`-scoped context, `ctx.calendar` (TW) otherwise — TW and
    US holiday sets diverge (`ingest/context.py`'s own `us_calendar` field comment), so this
    batch must never gate a US run against the TW calendar or vice versa."""
    return ctx.us_calendar if ctx.market is Market.US else ctx.calendar


def trailing_trading_days(ctx: IngestContext, run_date: date_, count: int) -> tuple[date_, ...]:
    """The `count` trading days ending at (and including) `run_date`, via the trading-day
    calendar so the window skips weekends/holidays rather than hand-rolling date math — same
    helper shape `screener/batch.py` uses."""
    calendar = calendar_for(ctx)
    start = calendar.shift_business_days(run_date, -(count - 1))
    return calendar.business_days_between(start, run_date)


# A composite momentum/quality/value score is not a meaningful product for a short-lived,
# leverage-derivative warrant series — and scoring the full warrant-inclusive universe is also
# what pushes this batch's per-symbol working set (bars/MA-series/signal dicts held for the
# whole universe at once, see `_score_and_persist_universe`) past the ~7.6GB it OOM-killed at.
# The classification rule (and its measured split) lives in `analytics/symbol_universe.py`,
# shared with every other reader of the raw `TaiwanStockPrice` universe.
# US statement datasets are a full re-pull every week, so the same (symbol, period, item) line
# sits in every snapshot of the window. Only the newest copy of each is read — see
# `ParquetStore.read_range_latest_by_key` for the memory this saves.
STATEMENT_COLUMNS: tuple[str, ...] = tuple(FinancialStatementLine.model_fields)


STATEMENT_KEY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "period_end",
    "statement_type",
    "period_type",
    "item",
)


PRICE_FRAME_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)


FLOW_FRAME_COLUMNS: tuple[str, ...] = ("symbol", "trade_date", "investor", "buy", "sell")


def bars_by_symbol(
    price_frame: pd.DataFrame, market: Market = Market.TW
) -> dict[str, tuple[PriceBar, ...]]:
    """Group the price window's rows by symbol and build each an ascending-by-date `PriceBar`
    series, from the columnar `read_range_frame` read (see that method's own docstring for why
    this reads a `DataFrame`, not `list[dict]` — this whole-market, 260-trading-day window is
    exactly the read `read_range`'s per-row `dict` boxing OOMs on). A row with no usable close
    is dropped rather than faked (matches the ingest mapper and `screener/batch.py`'s own
    `_bars_for_symbol`)."""
    bars_by_symbol: dict[str, tuple[PriceBar, ...]] = {}
    projected = cast("pd.DataFrame", price_frame.loc[:, list(PRICE_FRAME_COLUMNS)])
    # Drop warrant/derivative codes before any per-symbol grouping — a vectorized mask over the
    # whole frame, not a per-row Python check, so the excluded ~90% of rows
    # (`analytics.symbol_universe`'s own comment has the measured split) never reach
    # `PriceBar` construction at all. A no-op for US (see `scoreable_symbol_mask`'s own
    # docstring — the ingested US universe has no warrant/derivative pollution to exclude).
    scoreable = scoreable_symbol_mask(cast("pd.Series", projected["symbol"]), market)
    ordered = cast("pd.DataFrame", projected.loc[scoreable].sort_values("trade_date"))
    by_symbol: dict[str, list[PriceBar]] = defaultdict(list)
    # Positional-tuple unpacking (`name=None`), not attribute access: pandas' `itertuples()`
    # has no stub-backed field/row types (no `pandas-stubs` dependency here), so `row.close`
    # style access is unknown to pyright end to end. The explicit `tuple[object, ...]` cast
    # gives every unpacked name a concrete (if erased) type; the column order is guaranteed by
    # `PRICE_FRAME_COLUMNS`/the `.loc` reorder above.
    price_tuples = cast(
        "Iterable[tuple[object, object, object, object, object, object, object, object]]",
        ordered.itertuples(index=False, name=None),
    )
    for symbol, trade_date, open_, high, low, close, volume, turnover in price_tuples:
        del trade_date  # only used for the sort above; not part of PriceBar
        close_f = as_float(close)
        high_f = as_float(high)
        low_f = as_float(low)
        if close_f is None or close_f <= 0 or high_f is None or low_f is None:
            continue
        by_symbol[str(symbol)].append(
            PriceBar(
                high=high_f,
                low=low_f,
                close=close_f,
                volume=as_int(volume),
                open=as_float(open_),
                turnover=as_float(turnover),
            )
        )
    for symbol, bars in by_symbol.items():
        bars_by_symbol[symbol] = tuple(bars)
    return bars_by_symbol


def flow_rows_by_symbol(
    flow_frame: pd.DataFrame, market: Market
) -> dict[str, list[InstitutionalFlowRow]]:
    """The `DataFrame` counterpart of `bars_by_symbol`, same reasoning — see that function's
    docstring."""
    by_symbol: dict[str, list[InstitutionalFlowRow]] = defaultdict(list)
    projected = cast("pd.DataFrame", flow_frame.loc[:, list(FLOW_FRAME_COLUMNS)])
    # Same warrant/derivative exclusion as `bars_by_symbol` — see that function's docstring.
    scoreable = scoreable_symbol_mask(cast("pd.Series", projected["symbol"]))
    ordered = cast("pd.DataFrame", projected.loc[scoreable])
    # Positional unpacking — see `bars_by_symbol`'s docstring for why.
    flow_tuples = cast(
        "Iterable[tuple[object, object, object, object, object]]",
        ordered.itertuples(index=False, name=None),
    )
    for symbol, trade_date, investor, buy, sell in flow_tuples:
        if not isinstance(symbol, str) or not symbol or not isinstance(investor, str):
            continue
        if not isinstance(trade_date, str):
            continue
        by_symbol[symbol].append(
            InstitutionalFlowRow(
                symbol=symbol,
                market=market,
                trade_date=date_.fromisoformat(trade_date),
                investor=investor,
                buy=as_int(buy),
                sell=as_int(sell),
            )
        )
    return by_symbol


# Taiwan's statement partitions have no `period_type` column (FinMind reports one frequency, so the
# model defaults it to "quarterly"); asking the store for it would fail the read.
TW_STATEMENT_COLUMNS: tuple[str, ...] = tuple(c for c in STATEMENT_COLUMNS if c != "period_type")


# The columns a row must have a value in to pass `FinancialStatementLine`. `period_type` has a
# model default (quarterly), but only for an absent key: a row that carries the column with a null
# (the older US snapshots, about 800K rows) fails the model's literal check. The eager parse
# dropped those rows, so they are dropped here too.
_REQUIRED_STATEMENT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "market",
    "period_end",
    "period_type",
    "statement_type",
    "item",
    "origin_name",
    "value",
)


class LazyStatementLines(Mapping[str, list[FinancialStatementLine]]):
    """`symbol -> its statement lines`, with each symbol's lines built when asked for.

    The batch used to hold every symbol's lines at once: for Taiwan, 1.48M `FinancialStatementLine`
    objects built from 1.48M row dicts, which together took the process to 3.06 GB (measured on
    the dev data) before the job had touched prices, flows or any scoring, and the job was
    OOM-killed at a 4 GB cap. Scoring only ever asks for one symbol at a time (and for the set of
    symbols), so the rows stay in a columnar frame, with repeated text as categories, and the
    objects for a symbol exist only while that symbol is scored.

    Each symbol's rows keep the order the store returned them (datasets, then dates, then rows) and
    go through the same `parse_statement_lines` as before, so the lines are identical."""

    def __init__(self, frame: pd.DataFrame) -> None:
        # A row the model would reject (a missing value or label) is dropped up front, so a symbol
        # whose rows are all unusable is not listed at all: the symbols it lists are the ones the
        # eager parse listed, and the scoring universe is built from them.
        required = [c for c in _REQUIRED_STATEMENT_COLUMNS if c in frame.columns]
        frame = cast("pd.DataFrame", frame.loc[frame[required].notna().all(axis=1)])
        for column in cast("list[str]", list(frame.columns)):
            if column != "value":
                frame[column] = frame[column].astype("category")
        self._frame = frame
        self._positions: dict[str, object] = {
            str(symbol): positions
            for symbol, positions in frame.groupby(
                "symbol", sort=False, observed=True
            ).indices.items()
        }

    def __getitem__(self, symbol: str) -> list[FinancialStatementLine]:
        positions = self._positions[symbol]
        rows = cast("pd.DataFrame", self._frame.iloc[positions]).astype(object)
        # A missing value is None, as in a stored row; a NaN would validate as a number.
        records = cast("list[dict[str, object]]", rows.where(rows.notna(), None).to_dict("records"))
        return parse_statement_lines(records)

    def __iter__(self) -> Iterator[str]:
        return iter(self._positions)

    def __len__(self) -> int:
        return len(self._positions)


# Which `period_type` wins when a symbol has the same item for the same period as both a quarterly
# and an annual (or ttm) line, as US filings do at a fiscal year end. The first matching line is the
# one the fundamentals code uses, and the TTM sum is meant to add quarterly figures, so quarterly
# comes first. Without an order the row order differs from run to run (measured), and so could the
# line that wins.
US_STATEMENT_ORDER = (
    "symbol, period_end, statement_type, item, "
    "CASE period_type WHEN 'quarterly' THEN 0 WHEN 'ttm' THEN 1 ELSE 2 END"
)


def lazy_statement_lines(frames: Sequence[pd.DataFrame]) -> LazyStatementLines:
    """The frames of several statement datasets as one `LazyStatementLines`. Text columns become
    categories frame by frame before they are joined, so the joined frame never holds millions of
    separate strings at once (it is the difference between about 1 GB and a few hundred MB for the
    4M-row US window)."""
    parts = [frame.reset_index(drop=True) for frame in frames]
    names = cast("list[str]", list(parts[0].columns))
    joined: dict[str, object] = {}
    for column in (name for name in names if name != "value"):
        categorical = [cast("pd.Series", part[column]).astype("category") for part in parts]
        joined[column] = _union_categoricals(categorical)
    values = cast("pd.Series", pd.concat([part["value"] for part in parts], ignore_index=True))  # pyright: ignore[reportUnknownArgumentType]
    joined["value"] = values.to_numpy()
    return LazyStatementLines(pd.DataFrame(joined))


def statement_lines_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[FinancialStatementLine]]:
    by_symbol: dict[str, list[FinancialStatementLine]] = defaultdict(list)
    for line in parse_statement_lines(rows):
        by_symbol[line.symbol].append(line)
    return by_symbol


def latest_market_value_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    latest: dict[str, MarketValueRow] = {}
    for row in parse_market_value_rows(rows):
        current = latest.get(row.symbol)
        if current is None or row.trade_date > current.trade_date:
            latest[row.symbol] = row
    return {symbol: float(row.market_value) for symbol, row in latest.items()}


def month_revenue_yoy_by_symbol(
    rows: Sequence[dict[str, object]], run_date: date_
) -> dict[str, float | None]:
    """Each symbol's revenue YoY as knowable on `run_date`: months not yet published (before the
    10th of the following month) are ignored, however early the store holds them."""
    by_symbol: dict[str, list[MonthRevenueRow]] = defaultdict(list)
    for row in parse_month_revenue_rows(rows):
        by_symbol[row.symbol].append(row)
    return {
        symbol: compute_revenue_yoy(symbol_rows, as_of=run_date)
        for symbol, symbol_rows in by_symbol.items()
    }


_ANALYST_CONSENSUS_FIELDS = frozenset(AnalystConsensus.model_fields)


_INSTITUTIONAL_HOLDER_FIELDS = frozenset(InstitutionalHolderRow.model_fields)


_INSIDER_ACTIVITY_FIELDS = frozenset(InsiderActivityRow.model_fields)


_SECTOR_CLASS_FIELDS = frozenset(SectorClassification.model_fields)


def latest_analyst_consensus_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, AnalystConsensus]:
    """This symbol's most recent `analyst_consensus` row from the read window — used by both the
    US `analyst_revision` 多空評分 factor and the US `analyst_revision` 趨勢評分 dimension (shared
    read, not a duplicate fetch)."""
    latest: dict[str, AnalystConsensus] = {}
    for raw in rows:
        try:
            row = AnalystConsensus.model_validate(_project(raw, _ANALYST_CONSENSUS_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or row.as_of > current.as_of:
            latest[row.symbol] = row
    return latest


def institutional_holders_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[InstitutionalHolderRow]]:
    """Every `institutional_holders` row in the read window, grouped by symbol — a snapshot of
    current top holders, not a time series (see `InstitutionalHolderRow`'s own docstring), so
    every row in the window is kept rather than deduplicated to "latest per symbol"."""
    by_symbol: dict[str, list[InstitutionalHolderRow]] = defaultdict(list)
    for raw in rows:
        try:
            row = InstitutionalHolderRow.model_validate(_project(raw, _INSTITUTIONAL_HOLDER_FIELDS))
        except ValueError:
            continue
        by_symbol[row.symbol].append(row)
    return dict(by_symbol)


def insider_activity_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[InsiderActivityRow]]:
    """Every `insider_activity` row in the read window, grouped by symbol."""
    by_symbol: dict[str, list[InsiderActivityRow]] = defaultdict(list)
    for raw in rows:
        try:
            row = InsiderActivityRow.model_validate(_project(raw, _INSIDER_ACTIVITY_FIELDS))
        except ValueError:
            continue
        by_symbol[row.symbol].append(row)
    return dict(by_symbol)


def sector_key_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, str]:
    """This symbol's most recently-ingested `sector_class` slug (`sector_key`) — `sector_class`
    rows have no `as_of` field (a symbol's sector rarely changes), so the last row read for a
    given symbol wins, matching `ParquetStore.read_range`'s own ascending-by-date read order."""
    by_symbol: dict[str, str] = {}
    for raw in rows:
        try:
            row = SectorClassification.model_validate(_project(raw, _SECTOR_CLASS_FIELDS))
        except ValueError:
            continue
        if row.sector_key is not None:
            by_symbol[row.symbol] = row.sector_key
    return by_symbol


def split_universe_by_symbol_type(
    universe: Sequence[str], market: Market = Market.TW
) -> tuple[list[str], list[str]]:
    """Every symbol's scoring branch is decided purely by its code shape (`classify_symbol_type`
    needs no store lookup) — the one place `job_scoring_batch` chooses which of the two factor
    sets (`StockScoreBreakdown` vs `EtfScoreBreakdown`) a symbol gets. `ETF_LEVERAGED`/
    `ETF_INVERSE` are grouped with plain `ETF` here (all three use the same `EtfScoreBreakdown`
    shape — see that model's own docstring). Every `Market.US` symbol is unconditionally
    `SymbolType.STOCK` (see that function's own docstring) — `etf_symbols` is always empty for
    US."""
    stock_symbols: list[str] = []
    etf_symbols: list[str] = []
    for symbol in universe:
        if classify_symbol_type(symbol, market) is SymbolType.STOCK:
            stock_symbols.append(symbol)
        else:
            etf_symbols.append(symbol)
    return stock_symbols, etf_symbols


def _project(row: dict[str, object], fields: frozenset[str]) -> dict[str, object]:
    """Keep only the model's own fields — `ParquetStore.read_range`'s Hive-partitioned glob read
    resurfaces the partition-key bookkeeping columns (`dataset`/`market`/`date`/`v`) alongside
    the written row data, which the frozen `extra="forbid"` capability models reject outright.
    Mirrors `analytics/fundamentals.py::_project` (duplicated, not imported — that module is a
    layer this one already depends on for other reasons, but the helper itself is trivial enough
    that importing it across would only obscure which model's fields is meant here)."""
    return {key: value for key, value in row.items() if key in fields}


_ETF_NAV_FIELDS = frozenset(EtfNavRow.model_fields)


_ETF_EXPENSE_FIELDS = frozenset(EtfExpenseInfo.model_fields)


_ACTIVE_ETF_HOLDING_FIELDS = frozenset(ActiveEtfHoldingRow.model_fields)


_ACTIVE_ETF_HOLDING_CHANGE_FIELDS = frozenset(ActiveEtfHoldingChangeRow.model_fields)


def latest_etf_nav_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """This symbol's most recent published premium/discount %, from whatever `etf_nav` rows
    fell in the read window — `None`-valued rows (TWSE published no estimate that day) are
    dropped, never treated as a 0% deviation."""
    latest: dict[str, EtfNavRow] = {}
    for raw in rows:
        try:
            row = EtfNavRow.model_validate(_project(raw, _ETF_NAV_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or row.as_of > current.as_of:
            latest[row.symbol] = row
    return {
        symbol: row.premium_discount_pct
        for symbol, row in latest.items()
        if row.premium_discount_pct is not None
    }


def latest_etf_expense_ratio_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """This symbol's most recent SITCA-disclosed approximate TER — SITCA's report only refreshes
    monthly, so `as_of` may be `None` on some upstream rows; a row with `as_of` set always wins
    over one without, and among dated rows the newer one wins."""
    latest: dict[str, EtfExpenseInfo] = {}
    for raw in rows:
        try:
            row = EtfExpenseInfo.model_validate(_project(raw, _ETF_EXPENSE_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or (
            row.as_of is not None and (current.as_of is None or row.as_of > current.as_of)
        ):
            latest[row.symbol] = row
    return {
        symbol: row.total_expense_ratio_pct_approx
        for symbol, row in latest.items()
        if row.total_expense_ratio_pct_approx is not None
    }


def active_etf_symbols(rows: Sequence[dict[str, object]]) -> set[str]:
    """Which ETF symbols had a constituent-holdings snapshot at all in the read window — the only
    ground truth for "is this an active ETF" (`ActiveEtfHoldingRow` is an active-ETF-only FinMind
    capability; a passive/leveraged/inverse ETF is never present here, see
    `_management_activity_factor`'s own docstring)."""
    symbols: set[str] = set()
    for raw in rows:
        try:
            row = ActiveEtfHoldingRow.model_validate(_project(raw, _ACTIVE_ETF_HOLDING_FIELDS))
        except ValueError:
            continue
        symbols.add(row.symbol)
    return symbols


def holdings_turnover_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """Each active ETF's holdings-turnover proxy over the read window: the sum of every
    constituent buy/sell share count's magnitude across every row for that ETF — a magnitude
    (how much the portfolio moved), not a net direction, since "management activity" is about
    how actively the fund trades, not which way."""
    turnover: dict[str, float] = defaultdict(float)
    for raw in rows:
        try:
            row = ActiveEtfHoldingChangeRow.model_validate(
                _project(raw, _ACTIVE_ETF_HOLDING_CHANGE_FIELDS)
            )
        except ValueError:
            continue
        turnover[row.symbol] += abs(row.buy) + abs(row.sell)
    return dict(turnover)


def ma_series_for(bars: Sequence[PriceBar]) -> dict[str, tuple[float | None, ...]]:
    return {f"ma{window}": sma(bars, window).values for window in _MA_WINDOWS}


def cross_sectional_universe(values: Mapping[str, float | None]) -> list[float]:
    return [value for value in values.values() if value is not None]


def fundamental_universe(
    metrics_by_symbol: Mapping[str, FundamentalMetrics], attrs: Sequence[str]
) -> dict[str, list[float]]:
    universe: dict[str, list[float]] = {attr: [] for attr in attrs}
    for metrics in metrics_by_symbol.values():
        for attr in attrs:
            value = getattr(metrics, attr)
            if value is not None:
                universe[attr].append(value)
    return universe
