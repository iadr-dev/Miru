"""The nightly screener-flag batch job — evaluates every TW symbol against every catalog tag for
one trading day and writes the true flags to the `screener_flags` derived dataset.

This is what makes "tap a tag, get instant results" fast: the expensive per-symbol indicator and
chip-streak evaluation happens once a day here, and the query-time seam just reads back the flag
partition. It reads price and institutional-flow history back from the derived store (never a live
per-symbol fetch — that would be a full-market-scan path), computes the pure predicates in
`events.py`/`chip_streaks.py`, and writes one row per `(symbol, tag)` that evaluates true in a
tidy/long layout: only true flags are stored, so adding a tag in a later dispatch needs no schema
change and an empty day stays a small partition.

Layer note: this lives under `analytics/` (derived-compute) and reaches the store through the
ingest layer's reader/writer seams — the same shape the theme-layer seeds use. It defines the
upstream dataset names as local constants rather than importing the scheduler's job specs, so the
compute layer does not depend on the daemon's wiring (matching `analytics/fundamentals.py`).

Leader- and calendar-gating live here at the call site, visibly, exactly as the whole-market jobs
do: a non-leader pass writes nothing, a non-trading day is a journaled skip with no computation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date as date_
from typing import cast

import pandas as pd
import structlog

from analytics import patterns
from analytics.coerce import as_float, as_int
from analytics.indicators import PriceBar
from analytics.screener import chip_streaks, events, momentum
from analytics.screener.catalog import (
    FOREIGN_CUMULATIVE_WINDOW,
    SMART_COMBOS,
    STREAK_DAYS,
)
from analytics.symbol_universe import scoreable_symbol_sql
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

SCREENER_FLAGS_DATASET = "screener_flags"

# Upstream derived datasets this job reads back. Stable FinMind dataset names, kept as local read
# keys rather than importing the ingest job specs, so this compute layer does not bind to the
# scheduler's wiring (the same decoupling `analytics/fundamentals.py` uses).
PRICE_EOD_DATASET = "TaiwanStockPrice"
INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
MARGIN_SHORT_DATASET = "TaiwanStockMarginPurchaseShortSale"
SHAREHOLDING_DISTRIBUTION_DATASET = "TaiwanStockHoldingSharesPer"

# The same tier-label scheme
# `tools/market/holder_accumulation.py::_HOLDER_TIER_LABELS`/`_HOLDER_THRESHOLDS`
# confirms live against TDCC's own web query tool and FinMind's `TaiwanStockHoldingSharesPer` —
# duplicated here (not imported) since that module lives in `tools/`, which `analytics/` may not
# import from (dependency rule, `backend/CLAUDE.md` §3). Only the two boundaries this pass's tags
# need (<=100張 and >=1,000張), not the full 17-tier table.
_SMALL_HOLDER_TIER_LABELS: tuple[str, ...] = (
    "1-999",
    "1,000-5,000",
    "5,001-10,000",
    "10,001-15,000",
    "15,001-20,000",
    "20,001-30,000",
    "30,001-40,000",
    "40,001-50,000",
    "50,001-100,000",
)  # every tier whose upper bound is <= 100,000 shares (100張)
_LARGE_HOLDER_TIER_LABELS: tuple[str, ...] = ("1,000,001以上",)  # >= 1,000張 (1,000,000 shares)

# FinMind investor-category labels in the institutional-flow rows.
_FOREIGN_INVESTOR = "Foreign_Investor"
_INVESTMENT_TRUST = "Investment_Trust"
_DEALER_SELF = "Dealer_self"

# How many trailing trading days of history to read for the technical events. MACD (12/26/9) needs
# ~35 bars to define its histogram, and the slope/cross checks need two or three defined MA points
# on top of a 20-day window. The 熱門 52-week-high/low tags need `momentum.FIFTY_TWO_WEEK_WINDOW`
# (252) trading days plus one extra margin day, which dominates this constant — 260 trading days
# is a comfortable margin above that requirement that keeps the read bounded.
_PRICE_LOOKBACK_DAYS = 260

# The only price columns the flags read (`_bars_for_symbol`).
_PRICE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)

# Trailing trading days for the chip streaks — the longest chip window (the 5-day cumulative) plus
# a small margin so a missing partition inside the window still leaves enough real days.
_CHIP_LOOKBACK_DAYS = 12


def _trade_date_key(row: dict[str, object]) -> str:
    value = row.get("trade_date")
    return value if isinstance(value, str) else str(value)


def _bars_for_symbol(rows: Sequence[dict[str, object]]) -> tuple[PriceBar, ...]:
    """Build an ascending-by-date `PriceBar` series for one symbol from its raw stored price rows.
    A row with no usable close is dropped rather than faked (matches the ingest mapper, which
    already filters no-trade zero-close days)."""
    ordered = sorted(rows, key=_trade_date_key)
    bars: list[PriceBar] = []
    for row in ordered:
        close = as_float(row.get("close"))
        high = as_float(row.get("high"))
        low = as_float(row.get("low"))
        if close is None or close <= 0 or high is None or low is None:
            continue
        bars.append(
            PriceBar(
                high=high,
                low=low,
                close=close,
                volume=as_int(row.get("volume")),
                open=as_float(row.get("open")),
                turnover=as_float(row.get("turnover")),
            )
        )
    return tuple(bars)


def _daily_net_by_investor(rows: Sequence[dict[str, object]]) -> dict[str, list[float]]:
    """Per investor category, the ascending-by-date net-buy (buy minus sell) series for one
    symbol. One value per trading day present in the read window."""
    by_investor: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        investor = row.get("investor")
        if not isinstance(investor, str):
            continue
        day = _trade_date_key(row)
        net = float(as_int(row.get("buy")) - as_int(row.get("sell")))
        # A category can appear once per (symbol, day); sum defensively in case of duplicates.
        by_investor[investor][day] = by_investor[investor].get(day, 0.0) + net
    return {
        investor: [net for _, net in sorted(per_day.items())]
        for investor, per_day in by_investor.items()
    }


def _technical_flags(bars: Sequence[PriceBar]) -> list[str]:
    flags: list[str] = []
    if events.ma_turned_up(bars, 5):
        flags.append("ma5_turning_up")
    if events.ma_turned_down(bars, 5):
        flags.append("ma5_turning_down")
    if events.ma_crossed_up(bars, 5, 20):
        flags.append("golden_cross_5_20")
    if events.ma_crossed_down(bars, 5, 20):
        flags.append("death_cross_5_20")
    if events.macd_histogram_crossed_positive(bars):
        flags.append("macd_histogram_turned_positive")
    if events.macd_histogram_crossed_negative(bars):
        flags.append("macd_histogram_turned_negative")
    if events.breakout_above_ma(bars, 20):
        flags.append("breakout_above_ma20")
    if events.kd_golden_cross(bars):
        flags.append("kd_golden_cross")
    if events.kd_death_cross(bars):
        flags.append("kd_death_cross")
    flags.extend(_pattern_flags(bars))
    return flags


def _pattern_flags(bars: Sequence[PriceBar]) -> list[str]:
    flags: list[str] = []
    if patterns.is_bullish_engulfing(bars):
        flags.append("bullish_engulfing")
    if patterns.is_bearish_engulfing(bars):
        flags.append("bearish_engulfing")
    if patterns.is_bullish_cannon(bars):
        flags.append("bullish_cannon")
    if patterns.is_bearish_cannon(bars):
        flags.append("bearish_cannon")
    if patterns.is_v_reversal(bars):
        flags.append("v_reversal")
    if patterns.is_inverse_v_reversal(bars):
        flags.append("inverse_v_reversal")
    if patterns.is_three_rising(bars):
        flags.append("three_rising")
    if patterns.is_three_falling(bars):
        flags.append("three_falling")
    if patterns.is_doji(bars):
        flags.append("doji")
    return flags


def _hot_flags(bars: Sequence[PriceBar]) -> list[str]:
    flags: list[str] = []
    if momentum.volume_surge(bars):
        flags.append("volume_surge")
    if momentum.volume_contraction(bars):
        flags.append("volume_contraction")
    if momentum.new_52w_high(bars):
        flags.append("new_52w_high")
    if momentum.new_52w_low(bars):
        flags.append("new_52w_low")
    return flags


def _smart_flags(existing_tags: Sequence[str]) -> list[str]:
    """The pre-bundled 智能 combo tags whose underlying required tags are ALL already present in
    `existing_tags` (the already-assembled technical/pattern/chip/hot flags for this symbol on
    this run). A pure set-intersection check over already-computed booleans — never re-derives any
    underlying predicate — so a combo tag is only ever as correct as its ingredients."""
    present = set(existing_tags)
    return [combo for combo, required in SMART_COMBOS.items() if required <= present]


def _chip_flags(net_by_investor: dict[str, list[float]]) -> list[str]:
    flags: list[str] = []
    foreign = net_by_investor.get(_FOREIGN_INVESTOR, [])
    trust = net_by_investor.get(_INVESTMENT_TRUST, [])
    dealer = net_by_investor.get(_DEALER_SELF, [])
    if chip_streaks.net_buy_streak(foreign, STREAK_DAYS):
        flags.append("foreign_net_buy_streak_3")
    if chip_streaks.net_sell_streak(foreign, STREAK_DAYS):
        flags.append("foreign_net_sell_streak_3")
    if chip_streaks.net_buy_streak(trust, STREAK_DAYS):
        flags.append("trust_net_buy_streak_3")
    if chip_streaks.net_sell_streak(trust, STREAK_DAYS):
        flags.append("trust_net_sell_streak_3")
    if chip_streaks.net_buy_streak(dealer, STREAK_DAYS):
        flags.append("dealer_net_buy_streak_3")
    if chip_streaks.net_sell_streak(dealer, STREAK_DAYS):
        flags.append("dealer_net_sell_streak_3")
    if chip_streaks.cumulative_net_buy(foreign, FOREIGN_CUMULATIVE_WINDOW):
        flags.append("foreign_net_buy_5d_cumulative")
    return flags


def _daily_balances_by_field(rows: Sequence[dict[str, object]], field: str) -> list[float]:
    """One symbol's ascending-by-date series for a single `MarginShortRow` balance field (e.g.
    `margin_purchase_today_balance`) — one value per trading day present in the read window."""
    by_day: dict[str, float] = {}
    for row in rows:
        day = _trade_date_key(row)
        value = as_float(row.get(field))
        if value is not None:
            by_day[day] = value
    return [value for _, value in sorted(by_day.items())]


def _balance_rising_streak(daily_balances: Sequence[float], min_days: int) -> bool:
    """The last `min_days` day-over-day changes were each strictly positive — "連N日增加" for a
    plain balance series (distinct from `chip_streaks.net_buy_streak`, which streaks on the sign
    of an already-net buy/sell flow rather than a balance level's own deltas)."""
    if len(daily_balances) < min_days + 1:
        return False
    window = daily_balances[-(min_days + 1) :]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    return all(delta > 0 for delta in deltas)


def _balance_falling_streak(daily_balances: Sequence[float], min_days: int) -> bool:
    """The "連N日減少" mirror of `_balance_rising_streak`."""
    if len(daily_balances) < min_days + 1:
        return False
    window = daily_balances[-(min_days + 1) :]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    return all(delta < 0 for delta in deltas)


def _margin_flags(margin_rows: Sequence[dict[str, object]]) -> list[str]:
    flags: list[str] = []
    margin_balances = _daily_balances_by_field(margin_rows, "margin_purchase_today_balance")
    short_balances = _daily_balances_by_field(margin_rows, "short_sale_today_balance")
    if _balance_rising_streak(margin_balances, STREAK_DAYS):
        flags.append("margin_purchase_balance_streak_up_3")
    if _balance_falling_streak(short_balances, STREAK_DAYS):
        flags.append("short_sale_balance_streak_down_3")
    return flags


def _holder_tier_percent(
    rows: Sequence[dict[str, object]], tier_labels: Sequence[str]
) -> float | None:
    """This weekly snapshot's summed `percent`（占集保庫存數比例%）across `tier_labels` — `None`
    if the snapshot carried none of the wanted tiers (never asserted as `0.0`, which would read
    as a genuine "held by nobody" rather than "no data this week")."""
    wanted = set(tier_labels)
    total = 0.0
    seen = False
    for row in rows:
        level = row.get("holding_shares_level")
        percent = row.get("percent")
        if level in wanted and isinstance(percent, (int, float)):
            total += float(percent)
            seen = True
    return total if seen else None


def _holder_share_rising_flags(
    weekly_snapshots: Sequence[Sequence[dict[str, object]]],
) -> list[str]:
    """`weekly_snapshots` is this ONE symbol's rows from each of the last 4 weekly 集保 snapshots
    (oldest first) — 4 snapshots give 3 week-over-week deltas, the minimum needed to prove a
    genuine 3-week rising streak rather than a single-week blip."""
    small_series = [
        _holder_tier_percent(rows, _SMALL_HOLDER_TIER_LABELS) for rows in weekly_snapshots
    ]
    large_series = [
        _holder_tier_percent(rows, _LARGE_HOLDER_TIER_LABELS) for rows in weekly_snapshots
    ]
    flags: list[str] = []
    if all(v is not None for v in small_series) and _balance_rising_streak(
        cast("list[float]", small_series), STREAK_DAYS
    ):
        flags.append("small_holder_share_rising_3w")
    if all(v is not None for v in large_series) and _balance_rising_streak(
        cast("list[float]", large_series), STREAK_DAYS
    ):
        flags.append("large_holder_share_rising_3w")
    return flags


def _symbol_tags(
    *,
    price_rows: Sequence[dict[str, object]],
    flow_rows: Sequence[dict[str, object]],
    margin_rows: Sequence[dict[str, object]],
    shareholding_snapshots: Sequence[Sequence[dict[str, object]]],
    enough_shareholding_snapshots: bool,
) -> list[str]:
    """Every tag one symbol earns from its own already-grouped rows across all four sources —
    pulled out of `compute_flag_rows`'s per-symbol loop purely to keep that loop's own
    branch/statement count low; no behavior of its own beyond the source-by-source dispatch each
    line already documents."""
    tags: list[str] = []
    bars = _bars_for_symbol(price_rows)
    if bars:
        tags.extend(_technical_flags(bars))
        tags.extend(_hot_flags(bars))
    net_by_investor = _daily_net_by_investor(flow_rows)
    if net_by_investor:
        tags.extend(_chip_flags(net_by_investor))
    if margin_rows:
        tags.extend(_margin_flags(margin_rows))
    if shareholding_snapshots and enough_shareholding_snapshots:
        tags.extend(_holder_share_rising_flags(shareholding_snapshots))
    # Smart combos are evaluated last, over the already-assembled tag list above, so they can
    # only fire on tags this same pass has already proven true for this symbol.
    tags.extend(_smart_flags(tags))
    return tags


def compute_flag_rows(
    price_rows: Sequence[dict[str, object]],
    flow_rows: Sequence[dict[str, object]],
    margin_rows: Sequence[dict[str, object]] = (),
    weekly_shareholding_snapshots: Sequence[Sequence[dict[str, object]]] = (),
) -> list[dict[str, object]]:
    """Pure core: given the raw price/flow/margin rows read across the trailing window (plus up
    to 4 weekly shareholding-distribution snapshots, oldest first), return the tidy `(symbol,
    tag)` flag rows for every symbol/tag that evaluates true. Sorted by `(symbol, tag)` so the
    partition is deterministic for a given input."""
    price_by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in price_rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol:
            price_by_symbol[symbol].append(row)

    flow_by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in flow_rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol:
            flow_by_symbol[symbol].append(row)

    margin_by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in margin_rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol:
            margin_by_symbol[symbol].append(row)

    # Per symbol, this symbol's own rows from each weekly snapshot, in the same oldest-first order
    # as `weekly_shareholding_snapshots` itself.
    shareholding_by_symbol: dict[str, list[list[dict[str, object]]]] = defaultdict(
        lambda: [[] for _ in weekly_shareholding_snapshots]
    )
    for week_index, snapshot_rows in enumerate(weekly_shareholding_snapshots):
        for row in snapshot_rows:
            symbol = row.get("symbol")
            if isinstance(symbol, str) and symbol:
                shareholding_by_symbol[symbol][week_index].append(row)

    all_symbols = (
        price_by_symbol.keys()
        | flow_by_symbol.keys()
        | margin_by_symbol.keys()
        | shareholding_by_symbol.keys()
    )
    enough_snapshots = len(weekly_shareholding_snapshots) >= STREAK_DAYS + 1
    flag_rows: list[dict[str, object]] = []
    for symbol in all_symbols:
        tags = _symbol_tags(
            price_rows=price_by_symbol.get(symbol, []),
            flow_rows=flow_by_symbol.get(symbol, []),
            margin_rows=margin_by_symbol.get(symbol, []),
            shareholding_snapshots=shareholding_by_symbol.get(symbol, []),
            enough_shareholding_snapshots=enough_snapshots,
        )
        for tag in tags:
            flag_rows.append({"symbol": symbol, "tag": tag})

    flag_rows.sort(key=lambda row: (str(row["symbol"]), str(row["tag"])))
    return flag_rows


async def job_screener_flags(run_date: date_, ctx: IngestContext) -> JobRun:
    """Compute and write the `screener_flags` partition for `run_date`. Leader- and calendar-
    gated exactly like the whole-market pulls: a non-trading day is a journaled skip; a non-leader
    pass writes nothing. Reads price/flow history back from the derived store the earlier same-day
    jobs already wrote, so the daemon must schedule this after both of them.

    Idempotent: re-running for the same date rewrites the same partition version rather than
    accumulating rows. Uses the extrapolating check since `run_date` is almost always "today",
    which is always beyond `ctx.calendar`'s known range until today's own whole-market pulls
    have landed — see `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            SCREENER_FLAGS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            SCREENER_FLAGS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    market = ctx.market.value
    price_window = _trailing_trading_days(ctx, run_date, _PRICE_LOOKBACK_DAYS)
    chip_window = _trailing_trading_days(ctx, run_date, _CHIP_LOOKBACK_DAYS)
    price_rows = _scoreable_price_rows(ctx, market, price_window)
    flow_rows = ctx.store.read_range(INSTITUTIONAL_FLOW_DATASET, market, chip_window)
    margin_rows = ctx.store.read_range(MARGIN_SHORT_DATASET, market, chip_window)

    # Up to 4 weekly shareholding-distribution snapshots (oldest first) — 4 snapshots give 3
    # week-over-week deltas, the minimum needed to prove a genuine 3-week rising streak. Reads
    # whichever already-ingested snapshot dates exist on/before `run_date`; a market with fewer
    # than 4 ingested snapshots yet simply degrades to "not enough history" in `compute_flag_rows`
    # rather than erroring.
    shareholding_dates = [
        d
        for d in ctx.store.list_partition_dates(SHAREHOLDING_DISTRIBUTION_DATASET, market)
        if d <= run_date
    ][-(STREAK_DAYS + 1) :]
    weekly_shareholding_snapshots = [
        ctx.store.read_partition(SHAREHOLDING_DISTRIBUTION_DATASET, market, d)
        for d in shareholding_dates
    ]

    if not price_rows and not flow_rows and not margin_rows and not weekly_shareholding_snapshots:
        # No upstream history landed for this window — nothing to evaluate. A real, honest empty
        # write (rows=0) rather than an error: the next day's run recomputes once the price/flow
        # partitions this depends on exist.
        rows_written = ctx.store.write_partition(SCREENER_FLAGS_DATASET, market, run_date, [])
        return journal_run(
            SCREENER_FLAGS_DATASET,
            run_date,
            rows_written=rows_written,
            status=RunStatus.SUCCESS,
            detail="no upstream price/flow history in the read window — empty flag partition",
        )

    flag_rows = compute_flag_rows(price_rows, flow_rows, margin_rows, weekly_shareholding_snapshots)
    rows_written = ctx.store.write_partition(SCREENER_FLAGS_DATASET, market, run_date, flag_rows)
    logger.info(
        "screener_flags_computed",
        market=market,
        run_date=run_date.isoformat(),
        rows_written=rows_written,
    )
    return journal_run(
        SCREENER_FLAGS_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"evaluated the tag catalog over {market} price/flow history",
    )


def _scoreable_price_rows(
    ctx: IngestContext, market: str, window: Sequence[date_]
) -> list[dict[str, object]]:
    """The price window as the row dicts `compute_flag_rows` takes, for real equities and ETFs only.

    This used to be `store.read_range(...)`: every row of 260 trading days as a Python dict, about
    5.7M of them (1.1 KB each, **about 6.3 GB** measured on the dev data), 90% of them warrants
    whose flags nothing reads (the screener's universe is the scoreable symbols). DuckDB now drops
    the warrants while reading and returns only the columns the flags use as a frame (about 10% of
    the rows), which is turned into dicts here."""
    frame = ctx.store.read_range_frame(
        PRICE_EOD_DATASET,
        market,
        window,
        columns=_PRICE_COLUMNS,
        where=scoreable_symbol_sql(ctx.market),
    )
    if frame.empty:
        return []
    if pd.api.types.is_datetime64_any_dtype(frame["trade_date"]):
        frame["trade_date"] = frame["trade_date"].dt.date
    # A missing value is None, as it always was in a stored row: a NaN would pass `close <= 0`.
    cleaned = frame.astype(object).where(frame.notna(), None)
    return cast("list[dict[str, object]]", cleaned.to_dict("records"))


def _trailing_trading_days(ctx: IngestContext, run_date: date_, count: int) -> tuple[date_, ...]:
    """The `count` trading days ending at (and including) `run_date`, using the trading-day
    calendar so the window skips weekends/holidays rather than hand-rolling date math."""
    start = ctx.calendar.shift_business_days(run_date, -(count - 1))
    return ctx.calendar.business_days_between(start, run_date)


__all__ = [
    "INSTITUTIONAL_FLOW_DATASET",
    "PRICE_EOD_DATASET",
    "SCREENER_FLAGS_DATASET",
    "compute_flag_rows",
    "job_screener_flags",
]
