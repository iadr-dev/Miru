"""`get_watchlist_weekly_review` — the user's own watchlist, reviewed for the week.

A `private` tool in the family of `get_my_*` (`account_tools.py`): it acts for the signed-in user
only (the id comes from the `ToolContext`), is free at every signed-in tier and is not offered over
MCP. For every symbol in the user's watchlist groups it returns, computed in code from the stored
data (`analytics.watchlist_review`):

1. the week's close-to-close move (latest trading day against the one a week earlier), with the
   best and worst few and the symbols that have no figure;
2. the events due in the next seven days (ex-dividend / ex-rights dates, shareholder meetings, cash
   capital increase record dates, disposition periods, the statutory financial-report and monthly
   revenue deadlines), each with its source and date;
3. a freshness line per market, and a plain statement for every source that could not be read.

Prices and dividends/disposition rows come from the ingest-owned Parquet store (never a per-symbol
live fetch); only the exchange calendar feed goes through the data-access router, a bounded number
of symbols at a time, and its failure is reported, not hidden. The report text is rendered by code
too, so the model relays figures it did not produce.
"""

from __future__ import annotations

import asyncio
from calendar.events import fetch_feed_events
from calendar.models import CalendarEvent, EventType
from calendar.trading_days import TradingDayCalendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Final, Literal
from zoneinfo import ZoneInfo

import anyio.to_thread
import structlog
from pydantic import BaseModel, ConfigDict, Field

from analytics.watchlist_review import (
    DEFAULT_EXTREMES,
    EVENT_WINDOW_DAYS,
    SOURCE_ATTENTION,
    SOURCE_CALENDAR,
    SOURCE_DISPOSITION,
    SOURCE_DIVIDEND,
    SOURCE_US_DIVIDEND,
    EventKind,
    Freshness,
    MarketReview,
    ReviewEvent,
    ReviewStatus,
    SourceIssue,
    WeeklyMove,
    WeeklyReview,
    event_window,
    freshness_of,
    render_notice,
    render_report,
    select_events,
    statutory_deadlines,
    week_bounds,
    weekly_move,
)
from dataaccess.router import DataAccessRouter
from ingest.jobs import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    DIVIDENDS_JOB,
)
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import (
    AttentionStockRow,
    DispositionEventRow,
    DividendEvent,
    EODBar,
)
from schemas.enums import DataClass, Market, UserMarket
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools.account_tools import (
    MAX_QUOTED_SYMBOLS,
    MAX_WATCHLIST_ITEMS,
    AccountDataReader,
    WatchlistGroupSummary,
)
from tools.base import BaseTool, Tool, ToolContext, parse_args, require_user_id
from tools.market._common import (
    names_by_symbol_for,
    price_eod_dataset_for,
    real_calendar,
)

logger = structlog.get_logger(__name__)

# Distinct symbols reviewed per run - the same bound `get_my_watchlists` puts on quotes. The true
# count is always reported; symbols past the cap are named as not covered, never silently dropped.
MAX_REVIEW_SYMBOLS: Final = MAX_QUOTED_SYMBOLS
# Calendar-feed lookups run a few at a time and each is bounded, so a slow feed cannot stall a run.
_FEED_CONCURRENCY: Final = 6
_FEED_TIMEOUT_SECONDS: Final = 12.0
# A risk-list dataset older than this many days before the run is reported as not current.
_RISK_LIST_MAX_AGE_DAYS: Final = 7
# Daily bars land after the close; a run before this local hour expects the previous trading day.
_DATA_READY_HOUR: Final = 18
_TAIPEI: Final = ZoneInfo("Asia/Taipei")
_MARKET_TZ: Final[dict[Market, ZoneInfo]] = {
    Market.TW: _TAIPEI,
    Market.US: ZoneInfo("America/New_York"),
}
US_DIVIDENDS_DATASET: Final = "us_dividends"

_CAVEAT: Final = (
    "Read from the user's own watchlists and the stored end-of-day data at the time of the call: "
    "closes are not real-time and are not adjusted for splits or capital reductions. Event dates "
    "are the published schedule, not a forecast. Descriptive context only, not investment advice."
)


class GetWatchlistWeeklyReviewInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: UserMarket | None = Field(
        default=None, description="Only this market's groups (TW or US); omit for all."
    )
    group: str | None = Field(
        default=None,
        max_length=60,
        description="Only the watchlist group with this exact name; omit for every group.",
    )
    top_n: int = Field(
        default=DEFAULT_EXTREMES, ge=1, le=5, description="How many top movers each way to list."
    )


class GetWatchlistWeeklyReviewOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signed_in: Literal[True] = True
    # True when the user has no watchlist symbol at all: a normal state, not an error.
    nothing_to_review: bool
    review: WeeklyReview
    # The full report, rendered by code. Relay it as written.
    report: str
    # A one-paragraph summary suited to a notification.
    notice: str
    health_status: HealthStatus
    # Plain-language gaps (stale data, unreachable sources, symbols past the cap).
    caveats: tuple[str, ...] = Field(default_factory=tuple)
    as_of: datetime
    currentness_caveat: str = _CAVEAT


# ---------------------------------------------------------------------------------------------
# Store reads (blocking - run in a worker thread)
# ---------------------------------------------------------------------------------------------


class _MarketInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    moves: tuple[WeeklyMove, ...]
    events: tuple[ReviewEvent, ...]
    statuses: tuple[ReviewStatus, ...]
    freshness: Freshness
    issues: tuple[SourceIssue, ...]


def expected_trading_day(
    calendar: TradingDayCalendar, local_now: datetime, *, ready_hour: int = _DATA_READY_HOUR
) -> date:
    """The trading day whose close the run should already have: today's if it is a trading day and
    the data has had time to land, else the last trading day before today."""
    today = local_now.date()
    if local_now.time() >= time(ready_hour) and calendar.is_trading_day_extrapolating(today):
        return today
    return calendar.previous_trading_day(today)


def _bars_by_symbol(
    store: ParquetStore, market: Market, symbols: list[str], days: list[date]
) -> dict[str, list[tuple[date, float]]]:
    raw = store.read_range(price_eod_dataset_for(market.value), market.value, days, symbols=symbols)
    found: dict[str, list[tuple[date, float]]] = {}
    for bar in rows_from_store(EODBar, raw):
        if bar.market is market:
            found.setdefault(bar.symbol, []).append((bar.trade_date, bar.close))
    return found


def _price_section(
    store: ParquetStore, market: Market, symbols: list[str], names: dict[str, str], now: datetime
) -> tuple[tuple[WeeklyMove, ...], Freshness]:
    dataset = price_eod_dataset_for(market.value)
    dates = store.list_partition_dates(dataset, market.value)
    run_day = now.astimezone(_TAIPEI).date()
    base, end = week_bounds(dates, run_day)
    calendar = real_calendar(store, market.value)
    local_now = now.astimezone(_MARKET_TZ[market])
    expected = expected_trading_day(calendar, local_now) if dates else None
    freshness = freshness_of(market, end, expected, base)
    if end is None:
        moves = tuple(weekly_move(s, market, [], base_date=None, end_date=None) for s in symbols)
        return moves, freshness
    window = [d for d in dates if (base or end) <= d <= end]
    bars = _bars_by_symbol(store, market, symbols, window)
    moves = tuple(
        weekly_move(
            symbol,
            market,
            bars.get(symbol, []),
            base_date=base,
            end_date=end,
            name=names.get(symbol),
        )
        for symbol in symbols
    )
    return moves, freshness


def _latest_on_or_before(
    store: ParquetStore, dataset: str, market: Market, day: date
) -> date | None:
    dates = [d for d in store.list_partition_dates(dataset, market.value) if d <= day]
    return max(dates) if dates else None


@dataclass(frozen=True)
class _Scope:
    """What one market's reads are about: the reviewed symbols, their names, the event window and
    the run day (Taipei calendar day)."""

    market: Market
    symbols: tuple[str, ...]
    names: dict[str, str]
    window: tuple[date, date]
    run_day: date

    def in_window(self, day: date | None) -> bool:
        return day is not None and self.window[0] <= day <= self.window[1]


def _event(  # noqa: PLR0913 — one argument per field of the event
    scope: _Scope,
    symbol: str,
    kind: EventKind,
    day: date,
    *,
    source: str,
    detail: str | None = None,
) -> ReviewEvent:
    return ReviewEvent(
        symbol=symbol,
        market=scope.market,
        name=scope.names.get(symbol),
        kind=kind,
        event_date=day,
        source=source,
        detail=detail,
    )


def _no_data(source: str, market: Market) -> SourceIssue:
    return SourceIssue(source=source, market=market, detail="尚無已儲存的資料")


def _dividend_events(
    scope: _Scope, store: ParquetStore
) -> tuple[list[ReviewEvent], list[SourceIssue]]:
    tw = scope.market is Market.TW
    dataset = DIVIDENDS_JOB.dataset if tw else US_DIVIDENDS_DATASET
    source = SOURCE_DIVIDEND if tw else SOURCE_US_DIVIDEND
    dates = store.list_partition_dates(dataset, scope.market.value)
    if not dates:
        return [], [_no_data(source, scope.market)]
    wanted = set(scope.symbols)
    if tw:
        raw = store.read_range(dataset, scope.market.value, dates, symbols=list(scope.symbols))
    else:
        latest = store.read_partition(dataset, scope.market.value, max(dates))
        raw = [r for r in latest if r.get("symbol") in wanted]
    events: list[ReviewEvent] = []
    for row in rows_from_store(DividendEvent, raw):
        if row.symbol not in wanted:
            continue
        cash_day, stock_day = row.cash_ex_dividend_date, row.stock_ex_dividend_date
        if cash_day is not None and scope.in_window(cash_day):
            cash = f"現金股利每股 {row.cash_dividend:g}" if row.cash_dividend else None
            events.append(
                _event(scope, row.symbol, "ex_dividend", cash_day, source=source, detail=cash)
            )
        if stock_day is not None and scope.in_window(stock_day):
            stock = f"股票股利每股 {row.stock_dividend:g}" if row.stock_dividend else None
            events.append(
                _event(scope, row.symbol, "ex_rights", stock_day, source=source, detail=stock)
            )
    return events, []


def _stale_issue(source: str, market: Market, latest: date, run_day: date) -> list[SourceIssue]:
    if (run_day - latest).days > _RISK_LIST_MAX_AGE_DAYS:
        return [
            SourceIssue(source=source, market=market, detail=f"最新資料為 {latest.isoformat()}")
        ]
    return []


def _disposition_inputs(
    scope: _Scope, store: ParquetStore, as_of: date
) -> tuple[list[ReviewEvent], list[ReviewStatus], list[SourceIssue]]:
    """Disposition periods starting or ending in the window, and who is under disposition now."""
    market = scope.market
    latest = _latest_on_or_before(store, DISPOSITION_EVENTS_DATASET, market, scope.run_day)
    if latest is None:
        return [], [], [_no_data(SOURCE_DISPOSITION, market)]
    issues = _stale_issue(SOURCE_DISPOSITION, market, latest, scope.run_day)
    events: list[ReviewEvent] = []
    statuses: list[ReviewStatus] = []
    wanted = set(scope.symbols)
    raw = store.read_partition(DISPOSITION_EVENTS_DATASET, market.value, latest)
    for row in rows_from_store(DispositionEventRow, raw):
        if row.symbol not in wanted:
            continue
        span = f"處置期間 {row.period_start.isoformat()} 至 {row.period_end.isoformat()}"
        if scope.in_window(row.period_start):
            events.append(
                _event(
                    scope,
                    row.symbol,
                    "disposition_start",
                    row.period_start,
                    source=SOURCE_DISPOSITION,
                    detail=span,
                )
            )
        if scope.in_window(row.period_end):
            events.append(
                _event(
                    scope,
                    row.symbol,
                    "disposition_end",
                    row.period_end,
                    source=SOURCE_DISPOSITION,
                    detail=span,
                )
            )
        if row.period_start <= as_of <= row.period_end:
            statuses.append(
                ReviewStatus(
                    symbol=row.symbol,
                    market=market,
                    name=scope.names.get(row.symbol),
                    kind="disposition_active",
                    since=row.period_start,
                    until=row.period_end,
                    source=SOURCE_DISPOSITION,
                )
            )
    return events, statuses, issues


def _attention_inputs(
    scope: _Scope, store: ParquetStore
) -> tuple[list[ReviewStatus], list[SourceIssue]]:
    """Who was recently listed as an attention stock (the snapshot holds the last two days)."""
    market = scope.market
    latest = _latest_on_or_before(store, ATTENTION_STOCKS_DATASET, market, scope.run_day)
    if latest is None:
        return [], [_no_data(SOURCE_ATTENTION, market)]
    issues = _stale_issue(SOURCE_ATTENTION, market, latest, scope.run_day)
    wanted = set(scope.symbols)
    raw = store.read_partition(ATTENTION_STOCKS_DATASET, market.value, latest)
    statuses = [
        ReviewStatus(
            symbol=flag.symbol,
            market=market,
            name=scope.names.get(flag.symbol),
            kind="attention_listed",
            since=flag.flagged_date,
            source=SOURCE_ATTENTION,
        )
        for flag in rows_from_store(AttentionStockRow, raw)
        if flag.symbol in wanted
    ]
    return statuses, issues


def _load_market(
    store: ParquetStore,
    market: Market,
    symbols: list[str],
    window: tuple[date, date],
    now: datetime,
) -> _MarketInputs:
    run_day = now.astimezone(_TAIPEI).date()
    names = names_by_symbol_for(store, market.value)
    scope = _Scope(market, tuple(symbols), names, window, run_day)
    moves, freshness = _price_section(store, market, symbols, names, now)
    events, issues = _dividend_events(scope, store)
    statuses: list[ReviewStatus] = []
    if market is Market.TW:
        risk_events, statuses, risk_issues = _disposition_inputs(
            scope, store, freshness.data_as_of or run_day
        )
        attention, attention_issues = _attention_inputs(scope, store)
        events += risk_events
        events += statutory_deadlines(window[0], window[1], market)
        statuses += attention
        issues += risk_issues + attention_issues
    return _MarketInputs(
        moves=moves,
        events=tuple(events),
        statuses=tuple(statuses),
        freshness=freshness,
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------------------------
# The exchange calendar feed (router, bounded)
# ---------------------------------------------------------------------------------------------

_FEED_KINDS: Final[
    dict[EventType, Literal["shareholder_meeting", "capital_increase", "ex_rights"]]
] = {
    EventType.AGM: "shareholder_meeting",
    EventType.CASH_CAPITAL_INCREASE_RIGHTS: "capital_increase",
    EventType.DIVIDEND_EX_RIGHTS: "ex_rights",
}


def feed_events_to_review(
    events: tuple[CalendarEvent, ...], names: dict[str, str], covered: set[tuple[str, date]]
) -> list[ReviewEvent]:
    """Map calendar-feed events to review events. A feed ex-rights/ex-dividend date the stored
    dividend rows already list for that symbol and day is skipped so it is shown once."""
    mapped: list[ReviewEvent] = []
    for event in events:
        kind = _FEED_KINDS.get(event.type)
        day = event.event_date
        if kind is None or day is None or event.stock_id is None:
            continue
        if kind == "ex_rights" and (event.stock_id, day) in covered:
            continue
        mapped.append(
            ReviewEvent(
                symbol=event.stock_id,
                market=event.market,
                name=names.get(event.stock_id),
                kind=kind,
                event_date=day,
                source=SOURCE_CALENDAR,
                detail=event.title_zh if kind != "shareholder_meeting" else None,
            )
        )
    return mapped


async def _feed_for(
    router: DataAccessRouter,
    symbols: list[str],
    window: tuple[date, date],
    ctx: ToolContext,
) -> tuple[list[CalendarEvent], bool]:
    """Calendar-feed events for the symbols, and whether every lookup was served. A lookup that
    fails, times out or comes back unhealthy marks the feed as incomplete."""
    gate = asyncio.Semaphore(_FEED_CONCURRENCY)
    complete = True

    async def one(symbol: str) -> list[CalendarEvent]:
        nonlocal complete
        async with gate:
            try:
                async with asyncio.timeout(_FEED_TIMEOUT_SECONDS):
                    found, result = await fetch_feed_events(
                        router,
                        Symbol(id=symbol, market=Market.TW),
                        start_date=window[0],
                        end_date=window[1],
                        tls_policy=ctx.tls_policy,
                    )
            except Exception:
                logger.warning("weekly_review_calendar_feed_failed", exc_info=True)
                complete = False
                return []
        if result.health.status is not HealthStatus.OK:
            complete = False
        return list(found)

    batches = await asyncio.gather(*(one(symbol) for symbol in symbols))
    return [event for batch in batches for event in batch], complete


# ---------------------------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------------------------


def _distinct(groups: tuple[WatchlistGroupSummary, ...]) -> dict[Market, list[str]]:
    found: dict[Market, list[str]] = {Market.TW: [], Market.US: []}
    for group in groups:
        for entry in group.items:
            bucket = found[entry.market]
            if entry.symbol not in bucket:
                bucket.append(entry.symbol)
    return found


def _cap(distinct: dict[Market, list[str]], limit: int) -> dict[Market, list[str]]:
    """The first `limit` symbols across markets, Taiwan first, in watchlist order."""
    kept: dict[Market, list[str]] = {}
    room = limit
    for market in (Market.TW, Market.US):
        kept[market] = distinct[market][:room]
        room -= len(kept[market])
    return kept


def _caveats(review: WeeklyReview) -> tuple[str, ...]:
    notes: list[str] = []
    if review.symbols_covered < review.symbols_total:
        notes.append(
            f"watchlist has {review.symbols_total} symbols; "
            f"only {review.symbols_covered} were reviewed"
        )
    for section in review.markets:
        if section.freshness.stale:
            notes.append(f"{section.market.value} prices are older than the expected trading day")
        notes.extend(f"{i.source} unavailable: {i.detail}" for i in section.issues)
    return tuple(notes)


def _health(review: WeeklyReview) -> HealthStatus:
    if review.symbols_total and all(s.freshness.data_as_of is None for s in review.markets):
        return HealthStatus.UNAVAILABLE
    if _caveats(review):
        return HealthStatus.STALE
    return HealthStatus.OK


class GetWatchlistWeeklyReviewTool(BaseTool):
    name = "get_watchlist_weekly_review"
    description = (
        "The signed-in user's weekly watchlist review: for the symbols in their own watchlist "
        "groups (TW and US listed separately) each symbol's week move from stored closes (best "
        "and worst few, symbols without data), the events due in the next 7 days with source and "
        "date, and a data-freshness line. The `report` field is the finished text - relay it. "
        f"Reviews up to {MAX_REVIEW_SYMBOLS} symbols and reports the true count. Read-only; only "
        "the caller's own data; descriptive, never a recommendation."
    )
    input_model: type[BaseModel] = GetWatchlistWeeklyReviewInput
    output_model: type[BaseModel] = GetWatchlistWeeklyReviewOutput
    data_class = DataClass.PRIVATE
    card_type: str | None = None

    def __init__(
        self,
        reader: AccountDataReader,
        store: ParquetStore,
        router: DataAccessRouter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reader = reader
        self._store = store
        self._router = router
        self._clock = clock

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetWatchlistWeeklyReviewInput, args)
        user_id = require_user_id(ctx)
        now = self._clock()
        snapshot = await self._reader.watchlists(
            user_id=user_id, market=typed.market, max_items=MAX_WATCHLIST_ITEMS
        )
        groups = tuple(
            g
            for g in snapshot.groups
            if typed.group is None or g.name.casefold() == typed.group.strip().casefold()
        )
        distinct = _distinct(groups)
        hidden = max(snapshot.total_items - sum(len(g.items) for g in snapshot.groups), 0)
        total = sum(len(v) for v in distinct.values()) + (hidden if typed.group is None else 0)
        kept = _cap(distinct, MAX_REVIEW_SYMBOLS)
        run_day = now.astimezone(_TAIPEI).date()
        window = event_window(run_day, EVENT_WINDOW_DAYS)

        sections: list[MarketReview] = []
        for market in (Market.TW, Market.US):
            symbols = kept[market]
            if not distinct[market]:
                continue
            inputs = await anyio.to_thread.run_sync(
                _load_market, self._store, market, symbols, window, now
            )
            events = list(inputs.events)
            issues = list(inputs.issues)
            if market is Market.TW and symbols:
                feed, complete = await _feed_for(self._router, symbols, window, ctx)
                names = {m.symbol: m.name for m in inputs.moves if m.name}
                covered = {
                    (e.symbol, e.event_date) for e in events if e.symbol and e.kind == "ex_dividend"
                }
                events += feed_events_to_review(tuple(feed), names, covered)
                if not complete:
                    issues.append(
                        SourceIssue(
                            source=SOURCE_CALENDAR, market=market, detail="部分查詢逾時或失敗"
                        )
                    )
            sections.append(
                MarketReview(
                    market=market,
                    symbols_total=len(distinct[market]),
                    symbols_covered=len(symbols),
                    moves=inputs.moves,
                    events=select_events(events, *window),
                    statuses=inputs.statuses,
                    freshness=inputs.freshness,
                    issues=tuple(issues),
                )
            )
        review = WeeklyReview(
            run_date=run_day,
            window_start=window[0],
            window_end=window[1],
            groups=len(groups),
            symbols_total=total,
            symbols_covered=sum(len(v) for v in kept.values()),
            markets=tuple(sections),
            top_n=typed.top_n,
        )
        return GetWatchlistWeeklyReviewOutput(
            nothing_to_review=total == 0,
            review=review,
            report=render_report(review),
            notice=render_notice(review),
            health_status=_health(review),
            caveats=_caveats(review),
            as_of=now,
        )


def build_weekly_review_tool(
    reader: AccountDataReader, store: ParquetStore, router: DataAccessRouter
) -> Tool:
    return GetWatchlistWeeklyReviewTool(reader, store, router)


__all__ = [
    "MAX_REVIEW_SYMBOLS",
    "GetWatchlistWeeklyReviewInput",
    "GetWatchlistWeeklyReviewOutput",
    "GetWatchlistWeeklyReviewTool",
    "build_weekly_review_tool",
    "expected_trading_day",
    "feed_events_to_review",
]
