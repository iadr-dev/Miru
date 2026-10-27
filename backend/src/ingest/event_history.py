"""Event history: past daily snapshots of `attention_stocks` and `disposition_events`.

The daily jobs (`ingest/jobs/risk_lists.py`) can only take the exchanges' current-day lists, so the
stored feeds start the day they first ran. The exchanges publish the same lists WITH history by
date range (`sources/exchange_event_history`); this module rebuilds, for each past trading day
without a stored snapshot, the partition the daily job would have written that day, in the same
row shape, under the same partition key, in the same dataset. Every reader (screener gates,
watchlist exclusions, the disposition board, the event study) therefore sees a longer series
without a change.

What a snapshot of day D holds (checked against the real forward snapshots, module 22 section 1f):

- `disposition_events`: every disposition announced on or before D whose period has not ended
  before D (the exchange's current list drops a disposition once it is over).
- `attention_stocks`: the flags announced on D and on the trading day before it (the current
  list holds the last two announcement days).

Only a day with no partition is written (`only_missing`): a real forward snapshot, taken from the
live list on its day, is never replaced by a reconstruction, and a second run writes nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_

import structlog
from pydantic import BaseModel

from ingest.context import IngestContext
from ingest.jobs.risk_lists import ATTENTION_STOCKS_DATASET, DISPOSITION_EVENTS_DATASET
from ingest.jobs.whole_market import validate_rows_against
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import AttentionStockRow, DispositionEventRow
from schemas.enums import Capability, TLSPolicy
from sources.exchange_event_history import EventHistory, ExchangeEventHistoryAdapter
from sources.exchange_whole_market._common import ExchangeFetchError

logger = structlog.get_logger(__name__)

type Row = dict[str, object]

_DETAIL = "reconstructed from the exchange history reports"
# Days this close to today are written only as far as the reports prove them published: the
# reports trail the day, and an empty answer for a day not yet posted is not an empty list.
_SETTLE_DAYS = 10


def disposition_snapshot(history: Iterable[Row], day: date_) -> list[Row]:
    """The rows of the disposition list on `day`: announced on or before it, period not over."""
    day_iso = day.isoformat()
    current = {
        (str(row["security_type"]), str(row["symbol"]), str(row["period_start"])): row
        for row in history
        if str(row["announced_date"]) <= day_iso <= str(row["period_end"])
    }
    return [current[key] for key in sorted(current)]


def attention_snapshot(history: Iterable[Row], day: date_, previous_day: date_) -> list[Row]:
    """The rows of the attention list on `day`: the flags announced on `day` and on the trading
    day before it, one per security and announcement day."""
    wanted = {day.isoformat(), previous_day.isoformat()}
    current = {
        (str(row["flagged_date"]), str(row["security_type"]), str(row["symbol"])): row
        for row in history
        if str(row["flagged_date"]) in wanted
    }
    return [current[key] for key in sorted(current)]


def published_through(history: Iterable[Row], date_key: str) -> date_ | None:
    """The last announcement day BOTH exchanges' rows reach (the older of the two newest days), or
    None without any row: past that day the reports may simply not have been posted yet."""
    newest: dict[str, str] = {}
    for row in history:
        kind, day = str(row["security_type"]), str(row[date_key])
        newest[kind] = max(newest.get(kind, day), day)
    return date_.fromisoformat(min(newest.values())) if newest else None


def month_chunks(days: Sequence[date_]) -> list[list[date_]]:
    """The days grouped by calendar month, oldest month first (one request pair per month)."""
    by_month: dict[tuple[int, int], list[date_]] = defaultdict(list)
    for day in sorted(days):
        by_month[(day.year, day.month)].append(day)
    return [by_month[key] for key in sorted(by_month)]


@dataclass(frozen=True)
class _Feed:
    """One dataset: its row model, the capability its readers are told about, how the month's
    history is fetched and how a day's snapshot is cut from it."""

    dataset: str
    row_model: type[BaseModel]
    capability: Capability
    fetch: Callable[[date_, date_, TLSPolicy], Awaitable[EventHistory]]
    snapshot: Callable[[list[Row], date_, date_], list[Row]]
    date_key: str  # the row field that says on which day an announcement was made
    # The attention list of a day also holds the day before, which may sit in the month before.
    reaches_back_a_day: bool


def _feeds(source: ExchangeEventHistoryAdapter) -> tuple[_Feed, ...]:
    async def attention(start: date_, end: date_, tls: TLSPolicy) -> EventHistory:
        return await source.fetch_attention(start, end, tls_policy=tls)

    async def disposition(start: date_, end: date_, tls: TLSPolicy) -> EventHistory:
        return await source.fetch_disposition(start, end, tls_policy=tls)

    return (
        _Feed(
            ATTENTION_STOCKS_DATASET,
            AttentionStockRow,
            Capability.ATTENTION_STOCKS,
            attention,
            attention_snapshot,
            "flagged_date",
            reaches_back_a_day=True,
        ),
        _Feed(
            DISPOSITION_EVENTS_DATASET,
            DispositionEventRow,
            Capability.DISPOSITION_EVENTS,
            disposition,
            lambda history, day, _previous: disposition_snapshot(history, day),
            "announced_date",
            reaches_back_a_day=False,
        ),
    )


async def backfill_event_history(  # noqa: PLR0913 - one keyword per run dimension
    ctx: IngestContext,
    *,
    start: date_,
    end: date_,
    adapter: ExchangeEventHistoryAdapter | None = None,
    only_missing: bool = True,
    today: date_ | None = None,
) -> tuple[JobRun, ...]:
    """Write the missing daily snapshots of both datasets for the trading days in `start..end`.

    One request pair (TWSE + TPEx) per dataset and calendar month. A month whose reports cannot be
    read in full (an error from either exchange) writes nothing and is journaled FAILED, so a
    re-run fetches it again; the other months are unaffected. The newest days (within
    `_SETTLE_DAYS` of `today`) are written only up to the last day the reports reach on both
    exchanges, so a day the exchanges have not posted yet is left for the next run."""
    if not ctx.leader.is_leader():
        return (
            journal_run(
                DISPOSITION_EVENTS_DATASET,
                end,
                rows_written=0,
                status=RunStatus.SKIPPED,
                detail="not the leader - write skipped",
            ),
        )
    trading_days = ctx.calendar.business_days_between(start, end)
    settle_from = (today or datetime.now(UTC).date()) - timedelta(days=_SETTLE_DAYS)
    runs: list[JobRun] = []
    for feed in _feeds(adapter or ExchangeEventHistoryAdapter()):
        missing = [
            day
            for day in trading_days
            if not (only_missing and ctx.store.has_partition(feed.dataset, ctx.market.value, day))
        ]
        for days in month_chunks(missing):
            runs.extend(await _write_month(ctx, feed, days, settle_from))
    return tuple(runs)


async def _write_month(
    ctx: IngestContext, feed: _Feed, days: list[date_], settle_from: date_
) -> list[JobRun]:
    first = ctx.calendar.previous_trading_day(days[0]) if feed.reaches_back_a_day else days[0]
    try:
        history = await feed.fetch(first, days[-1], ctx.tls_policy)
    except ExchangeFetchError as exc:
        logger.warning(
            "event_history_month_failed",
            dataset=feed.dataset,
            first=days[0].isoformat(),
            reason=exc.detail,
        )
        failed = f"{len(days)} days from {days[0].isoformat()} not written: {exc.detail}"
        return [
            journal_run(
                feed.dataset, days[0], rows_written=0, status=RunStatus.FAILED, detail=failed
            )
        ]
    valid = validate_rows_against(feed.row_model, feed.dataset, history.rows, days[0])
    detail = f"{_DETAIL}; no data published" if history.no_data else _DETAIL
    runs: list[JobRun] = []
    if days[-1] >= settle_from:
        through = published_through(valid, feed.date_key)
        unposted = [day for day in days if through is None or day > through]
        if unposted:
            days = [day for day in days if day not in unposted]
            reach = through.isoformat() if through else "nothing"
            runs.append(
                journal_run(
                    feed.dataset,
                    unposted[0],
                    rows_written=0,
                    status=RunStatus.SKIPPED,
                    detail=(
                        f"{len(unposted)} days from {unposted[0].isoformat()} not posted yet "
                        f"(the reports reach {reach})"
                    ),
                )
            )
    for day in days:
        rows = feed.snapshot(valid, day, ctx.calendar.previous_trading_day(day))
        written = ctx.store.write_partition(feed.dataset, ctx.market.value, day, rows)
        runs.append(
            journal_run(
                feed.dataset, day, rows_written=written, status=RunStatus.SUCCESS, detail=detail
            )
        )
    if days:
        await ctx.data_update_publisher.publish_data_updated(
            capability=feed.capability,
            market=ctx.market,
            as_of=days[-1].isoformat(),
            scope="whole-market",
        )
    return runs


__all__ = [
    "attention_snapshot",
    "backfill_event_history",
    "disposition_snapshot",
    "month_chunks",
    "published_through",
]
