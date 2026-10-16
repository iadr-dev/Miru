"""The 08:00 pre-market job: today's strategy watchlist, one row per (symbol, strategy).

Reads the last completed session's price, institutional-flow and margin history plus the event
feeds back from the derived store (never a source: ingest is the only whole-market path), runs
the setups in `strategies.py`, drops the stocks `context.excluded_symbols` names, attaches the
context columns, and writes the `strategy_watchlist` partition dated today. The intraday check and
the morning-list alert read that partition.

Leader- and calendar-gated like the other batches. Refuses to publish a watchlist built on stale
prices (the last session more than a few trading days old), since every level would be wrong.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date as date_
from typing import cast

import structlog

from analytics.indicators import PriceBar
from analytics.scoring_batch.inputs import (
    PRICE_FRAME_COLUMNS,
    bars_by_symbol,
    calendar_for,
    trailing_trading_days,
)
from analytics.strategy_watch import context
from analytics.strategy_watch.strategies import (
    HOLDER_WEEKS,
    MIN_HISTORY,
    Regime,
    Setup,
    Strategy,
    closes_above_ma20,
    evaluate_setups,
    regime_from_breadth,
)
from analytics.symbol_universe import SymbolType, classify_symbol_type, scoreable_symbol_sql
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

STRATEGY_WATCHLIST_DATASET = "strategy_watchlist"

PRICE_EOD_DATASET = "TaiwanStockPrice"
STOCK_INFO_DATASET = "TaiwanStockInfo"
INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
MARGIN_SHORT_DATASET = "TaiwanStockMarginPurchaseShortSale"
ATTENTION_STOCKS_DATASET = "attention_stocks"
DISPOSITION_EVENTS_DATASET = "disposition_events"
DIVIDEND_DATASET = "TaiwanStockDividend"
MATERIAL_ANNOUNCEMENTS_DATASET = "material_announcements"
SHAREHOLDING_DISTRIBUTION_DATASET = "TaiwanStockHoldingSharesPer"

PRICE_LOOKBACK_SESSIONS = MIN_HISTORY + 20
CHIP_LOOKBACK_SESSIONS = 30
EVENT_LOOKBACK_SESSIONS = 5
DIVIDEND_LOOKBACK_SESSIONS = 120  # ex-dates are announced weeks ahead
EX_DIVIDEND_HORIZON_SESSIONS = 3
MAX_STALE_SESSIONS = 3
MAX_PER_STRATEGY = 30


def _by_symbol(rows: Sequence[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str):
            grouped[symbol].append(row)
    return grouped


def build_watchlist_rows(  # noqa: PLR0913 - one argument per input feed
    *,
    run_date: date_,
    as_of: date_,
    bars: Mapping[str, Sequence[PriceBar]],
    industry_by_symbol: Mapping[str, str],
    name_by_symbol: Mapping[str, str],
    flow_rows: Sequence[Mapping[str, object]],
    margin_rows: Sequence[Mapping[str, object]],
    exclusions: Mapping[str, str],
    headlines: Mapping[str, str],
    holder_snapshots: Sequence[Sequence[Mapping[str, object]]] = (),
    previous: Mapping[tuple[str, str], tuple[date_, int]] | None = None,
) -> tuple[list[dict[str, object]], Regime, float]:
    """The watchlist rows plus the market regime and breadth they were computed under. Pure, so
    the whole screen is testable without a store.

    `holder_snapshots` are the weekly shareholding-distribution partitions, oldest first.
    `previous` maps (symbol, strategy) on the last session's list to (first listed, days on list),
    so a stock that stays listed keeps counting instead of starting over every morning."""
    above = [flag for b in bars.values() if (flag := closes_above_ma20(b)) is not None]
    breadth = sum(above) / len(above) * 100.0 if above else 0.0
    regime = regime_from_breadth(breadth)

    flow_by_symbol = _by_symbol(flow_rows)
    margin_by_symbol = _by_symbol(margin_rows)
    holders_by_symbol: dict[str, list[list[Mapping[str, object]]]] = defaultdict(list)
    for snapshot in holder_snapshots:
        for symbol, snapshot_rows in _by_symbol(snapshot).items():
            holders_by_symbol[symbol].append(snapshot_rows)

    setups: list[Setup] = []
    for symbol, series in bars.items():
        if symbol in exclusions:
            continue
        # 籌碼 accumulation is about a company's shares changing hands. An ETF's holder count and
        # large-holder share move with market making and creations, so ETFs get no chip setup.
        chips = (
            context.chip_inputs(
                flow_by_symbol.get(symbol, []),
                margin_by_symbol.get(symbol, []),
                holders_by_symbol.get(symbol, []),
            )
            if classify_symbol_type(symbol) is SymbolType.STOCK
            else None
        )
        setups.extend(evaluate_setups(symbol, as_of, series, regime=regime, chips=chips))

    # Most liquid first, then a fixed cap per strategy: a short list a person can actually watch,
    # and the same order every run for the same data.
    by_strategy: dict[Strategy, list[Setup]] = defaultdict(list)
    for setup in sorted(setups, key=lambda s: (-(s.close * s.avg_volume_20d), s.symbol)):
        if len(by_strategy[setup.strategy]) < MAX_PER_STRATEGY:
            by_strategy[setup.strategy].append(setup)
    kept = [setup for group in by_strategy.values() for setup in group]
    strength = context.sector_strength(industry_by_symbol, bars)

    rows: list[dict[str, object]] = []
    for setup in kept:
        foreign, trust = context.chip_streaks(flow_by_symbol.get(setup.symbol, []))
        industry = industry_by_symbol.get(setup.symbol)
        carried = (previous or {}).get((setup.symbol, setup.strategy.value))
        first_listed, days = (carried[0], carried[1] + 1) if carried else (run_date, 1)
        row = setup.model_copy(update={"first_listed": first_listed, "days_on_list": days})
        values = row.model_dump(mode="json")
        values.update(
            {
                "run_date": run_date.isoformat(),
                "name": name_by_symbol.get(setup.symbol),
                "industry": industry,
                "sector_strength_pct": strength.get(industry) if industry else None,
                "foreign_streak": foreign,
                "trust_streak": trust,
                "margin_change_5d_pct": context.margin_change_pct(
                    margin_by_symbol.get(setup.symbol, [])
                ),
                "headline": headlines.get(setup.symbol),
                "regime": regime.value,
                "breadth_pct": breadth,
            }
        )
        rows.append(values)
    return rows, regime, breadth


def _previous_listing(
    ctx: IngestContext, market: str, last_run: date_
) -> dict[tuple[str, str], tuple[date_, int]]:
    """(symbol, strategy) -> (first listed, days on list) from the previous trading day's list.
    Only the run on `last_run` carries forward: after a gap the count starts again."""
    if not ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, market, last_run):
        return {}
    carried: dict[tuple[str, str], tuple[date_, int]] = {}
    for row in ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, market, last_run):
        first = context.as_date(row.get("first_listed")) or last_run
        days = row.get("days_on_list")
        count = days if isinstance(days, int) and not isinstance(days, bool) else 1
        carried[(str(row.get("symbol")), str(row.get("strategy")))] = (first, count)
    return carried


def _holder_snapshots(
    ctx: IngestContext, market: str, as_of: date_
) -> list[list[dict[str, object]]]:
    """The weekly shareholding-distribution partitions on or before `as_of`, oldest first: enough
    for a `HOLDER_WEEKS` comparison."""
    dates = [
        d
        for d in ctx.store.list_partition_dates(SHAREHOLDING_DISTRIBUTION_DATASET, market)
        if d <= as_of
    ][-(HOLDER_WEEKS + 1) :]
    return [ctx.store.read_partition(SHAREHOLDING_DISTRIBUTION_DATASET, market, d) for d in dates]


async def job_strategy_watchlist(run_date: date_, ctx: IngestContext) -> JobRun:
    """Write the `strategy_watchlist` partition for `run_date` (a trading-day morning)."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            STRATEGY_WATCHLIST_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            STRATEGY_WATCHLIST_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    market = ctx.market.value
    calendar = calendar_for(ctx)
    last_session = calendar.previous_trading_day(run_date)
    window = trailing_trading_days(ctx, last_session, PRICE_LOOKBACK_SESSIONS)
    frame = ctx.store.read_range_frame(
        PRICE_EOD_DATASET,
        market,
        window,
        columns=PRICE_FRAME_COLUMNS,
        where=scoreable_symbol_sql(ctx.market),
    )
    if frame.empty:
        return journal_run(
            STRATEGY_WATCHLIST_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no price history in the read window",
        )
    last_dates: dict[str, date_] = {}
    # Positional unpacking over a typed cast, the same way `scoring_batch.inputs` reads frames.
    pairs = cast(
        "Iterable[tuple[object, object]]",
        frame.loc[:, ["symbol", "trade_date"]].itertuples(index=False, name=None),
    )
    for symbol, value in pairs:
        day = context.as_date(value)
        if day is not None and (str(symbol) not in last_dates or day > last_dates[str(symbol)]):
            last_dates[str(symbol)] = day
    as_of = max(last_dates.values())
    stale_by = len(calendar.business_days_between(as_of, last_session)) - 1
    if stale_by > MAX_STALE_SESSIONS:
        return journal_run(
            STRATEGY_WATCHLIST_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=f"prices are stale (last session {as_of.isoformat()}), watchlist not published",
        )
    # Only symbols that traded on the last session: a suspended stock's levels are not current.
    bars = {s: b for s, b in bars_by_symbol(frame).items() if last_dates.get(s) == as_of}

    info = ctx.store.latest_partition_on_or_before(STOCK_INFO_DATASET, market, run_date)
    industry_by_symbol: dict[str, str] = {}
    name_by_symbol: dict[str, str] = {}
    if info is not None:
        for row in ctx.store.read_partition(STOCK_INFO_DATASET, market, info[0]):
            symbol, industry, name = (
                row.get("symbol"),
                row.get("industry_category"),
                row.get("name"),
            )
            if not isinstance(symbol, str):
                continue
            if isinstance(industry, str) and industry:
                industry_by_symbol[symbol] = industry
            if isinstance(name, str) and name:
                name_by_symbol[symbol] = name

    event_window = trailing_trading_days(ctx, run_date, EVENT_LOOKBACK_SESSIONS)
    exclusions = context.excluded_symbols(
        run_date=run_date,
        as_of=as_of,
        ex_dividend_until=calendar.shift_business_days(run_date, EX_DIVIDEND_HORIZON_SESSIONS),
        attention_rows=ctx.store.read_range(ATTENTION_STOCKS_DATASET, market, event_window),
        disposition_rows=ctx.store.read_range(DISPOSITION_EVENTS_DATASET, market, event_window),
        dividend_rows=ctx.store.read_range(
            DIVIDEND_DATASET,
            market,
            trailing_trading_days(ctx, run_date, DIVIDEND_LOOKBACK_SESSIONS),
        ),
    )
    headlines = context.latest_headlines(
        ctx.store.read_range(MATERIAL_ANNOUNCEMENTS_DATASET, market, event_window), since=as_of
    )

    candidates = sorted(set(bars) - set(exclusions))
    holder_snapshots = _holder_snapshots(ctx, market, as_of)
    previous = _previous_listing(ctx, market, calendar.previous_trading_day(run_date))
    chip_window = trailing_trading_days(ctx, as_of, CHIP_LOOKBACK_SESSIONS)
    flow_rows = ctx.store.read_range(
        INSTITUTIONAL_FLOW_DATASET, market, chip_window, symbols=candidates
    )
    margin_rows = ctx.store.read_range(
        MARGIN_SHORT_DATASET, market, chip_window, symbols=candidates
    )

    rows, regime, breadth = build_watchlist_rows(
        run_date=run_date,
        as_of=as_of,
        bars=bars,
        industry_by_symbol=industry_by_symbol,
        name_by_symbol=name_by_symbol,
        flow_rows=flow_rows,
        margin_rows=margin_rows,
        exclusions=exclusions,
        headlines=headlines,
        holder_snapshots=holder_snapshots,
        previous=previous,
    )
    rows_written = ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, market, run_date, rows)
    logger.info(
        "strategy_watchlist_computed",
        market=market,
        run_date=run_date.isoformat(),
        as_of=as_of.isoformat(),
        regime=regime.value,
        breadth_pct=round(breadth, 1),
        excluded=len(exclusions),
        rows_written=rows_written,
    )
    return journal_run(
        STRATEGY_WATCHLIST_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"{rows_written} setups from the {as_of.isoformat()} session, regime {regime.value}",
    )


__all__ = [
    "STRATEGY_WATCHLIST_DATASET",
    "build_watchlist_rows",
    "job_strategy_watchlist",
]
