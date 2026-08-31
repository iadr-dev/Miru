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

import structlog

from analytics import patterns
from analytics.indicators import PriceBar
from analytics.screener import chip_streaks, events, momentum
from analytics.screener.catalog import (
    FOREIGN_CUMULATIVE_WINDOW,
    SMART_COMBOS,
    STREAK_DAYS,
)
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

SCREENER_FLAGS_DATASET = "screener_flags"

# Upstream derived datasets this job reads back. Stable FinMind dataset names, kept as local read
# keys rather than importing the ingest job specs, so this compute layer does not bind to the
# scheduler's wiring (the same decoupling `analytics/fundamentals.py` uses).
PRICE_EOD_DATASET = "TaiwanStockPrice"
INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"

# FinMind investor-category labels in the institutional-flow rows.
_FOREIGN_INVESTOR = "Foreign_Investor"
_INVESTMENT_TRUST = "Investment_Trust"

# How many trailing trading days of history to read for the technical events. MACD (12/26/9) needs
# ~35 bars to define its histogram, and the slope/cross checks need two or three defined MA points
# on top of a 20-day window. The 熱門 52-week-high/low tags need `momentum.FIFTY_TWO_WEEK_WINDOW`
# (252) trading days plus one extra margin day, which dominates this constant — 260 trading days
# is a comfortable margin above that requirement that keeps the read bounded.
_PRICE_LOOKBACK_DAYS = 260

# Trailing trading days for the chip streaks — the longest chip window (the 5-day cumulative) plus
# a small margin so a missing partition inside the window still leaves enough real days.
_CHIP_LOOKBACK_DAYS = 12


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


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
        close = _as_float(row.get("close"))
        high = _as_float(row.get("high"))
        low = _as_float(row.get("low"))
        if close is None or close <= 0 or high is None or low is None:
            continue
        bars.append(
            PriceBar(
                high=high,
                low=low,
                close=close,
                volume=_as_int(row.get("volume")),
                open=_as_float(row.get("open")),
                turnover=_as_float(row.get("turnover")),
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
        net = float(_as_int(row.get("buy")) - _as_int(row.get("sell")))
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
    if chip_streaks.net_buy_streak(foreign, STREAK_DAYS):
        flags.append("foreign_net_buy_streak_3")
    if chip_streaks.net_sell_streak(foreign, STREAK_DAYS):
        flags.append("foreign_net_sell_streak_3")
    if chip_streaks.net_buy_streak(trust, STREAK_DAYS):
        flags.append("trust_net_buy_streak_3")
    if chip_streaks.cumulative_net_buy(foreign, FOREIGN_CUMULATIVE_WINDOW):
        flags.append("foreign_net_buy_5d_cumulative")
    return flags


def compute_flag_rows(
    price_rows: Sequence[dict[str, object]],
    flow_rows: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Pure core: given the raw price and flow rows read across the trailing window, return the
    tidy `(symbol, tag)` flag rows for every symbol/tag that evaluates true. Sorted by
    `(symbol, tag)` so the partition is deterministic for a given input."""
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

    flag_rows: list[dict[str, object]] = []
    for symbol in price_by_symbol.keys() | flow_by_symbol.keys():
        tags: list[str] = []
        bars = _bars_for_symbol(price_by_symbol.get(symbol, []))
        if bars:
            tags.extend(_technical_flags(bars))
            tags.extend(_hot_flags(bars))
        net_by_investor = _daily_net_by_investor(flow_by_symbol.get(symbol, []))
        if net_by_investor:
            tags.extend(_chip_flags(net_by_investor))
        # Smart combos are evaluated last, over the already-assembled tag list above, so they can
        # only fire on tags this same pass has already proven true for this symbol.
        tags.extend(_smart_flags(tags))
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
    price_rows = ctx.store.read_range(PRICE_EOD_DATASET, market, price_window)
    flow_rows = ctx.store.read_range(INSTITUTIONAL_FLOW_DATASET, market, chip_window)

    if not price_rows and not flow_rows:
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

    flag_rows = compute_flag_rows(price_rows, flow_rows)
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
