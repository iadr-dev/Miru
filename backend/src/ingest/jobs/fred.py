"""The US macro statistics ingest job: `job_fred_macro`, daily at 07:40 Taipei after the US jobs.

One job, four Parquet datasets (layout in `ingest/fred_store.py`):

1. **Series metadata** for every catalog series (title, units, frequency, seasonal adjustment,
   last-updated stamp, the release it belongs to), fetched concurrently and best-effort.
2. **Observations**: a series is re-pulled only when it changed - its metadata `last_updated`
   differs from the stored copy, or the change feed lists it, or it has no stored data yet. The
   first pull (and a full refresh every `FULL_REFRESH_EVERY_DAYS`) reads the whole five-year
   window; a changed series otherwise re-reads only the recent window that can still be revised.
   The result is merged into a complete snapshot, so the newest partition is always a full view.
3. **Vintages** for the revision-tracked series: every value each recent observation held, from
   which the tools show first release vs latest.
4. **Release dates**: the calendar (recent and upcoming) of the releases the catalog belongs to.

Contract: leader-gated; journals every dataset and returns one summary `JobRun`; skips cleanly
(journalled SKIPPED with the reason) when no API key is configured; idempotent (re-running the
same date rewrites the same partitions); an empty answer never replaces a non-empty partition
(`ParquetStore.has_rows`); a series whose fetch fails keeps its previously stored rows and makes
the run PARTIAL. Not calendar-gated here: the statistics publish on their own schedule, and the
daemon already gates the job on the US trading calendar.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta
from typing import cast

import structlog
from pydantic import ValidationError

from ingest.context import IngestContext
from ingest.fred_store import (
    MARKET,
    US_MACRO_META_DATASET,
    US_MACRO_OBSERVATIONS_DATASET,
    US_MACRO_RELEASE_DATES_DATASET,
    US_MACRO_VINTAGES_DATASET,
)
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import (
    MacroObservationRow,
    MacroReleaseDateRow,
    MacroSeriesMetaRow,
    MacroVintageRow,
)
from schemas.enums import Capability, Market
from schemas.fred_catalog import (
    LOOKBACK_YEARS,
    REVISION_SERIES,
    SERIES_CATALOG,
    MacroSeriesSpec,
    SeriesFrequency,
)
from schemas.source import HealthStatus, SourceResult
from sources.fred import LATEST_REALTIME, NOT_CONFIGURED_DETAIL

logger = structlog.get_logger(__name__)

JOB_NAME = "fred_macro"

FULL_REFRESH_EVERY_DAYS = 7
RELEASE_WINDOW_PAST_DAYS = 45
RELEASE_WINDOW_FUTURE_DAYS = 150
_SERIES_CONCURRENCY = 4
_FAILURES_SHOWN = 5

# How far back a changed series is re-read: wide enough to catch the revisions the statistical
# agencies make to recent observations (monthly figures are revised for about a year).
_OVERLAP_DAYS: dict[SeriesFrequency, int] = {
    SeriesFrequency.DAILY: 45,
    SeriesFrequency.WEEKLY: 90,
    SeriesFrequency.MONTHLY: 400,
    SeriesFrequency.QUARTERLY: 800,
}
# How many recent observations' vintage history is kept (about two years of months, three of
# quarters).
_VINTAGE_WINDOW_DAYS: dict[SeriesFrequency, int] = {
    SeriesFrequency.WEEKLY: 365,
    SeriesFrequency.MONTHLY: 760,
    SeriesFrequency.QUARTERLY: 1100,
}

type Row = dict[str, object]


@dataclass
class _SeriesOutcome:
    spec: MacroSeriesSpec
    series: Row | None = None  # the `series` metadata payload, None when the lookup failed
    release: Row | None = None
    rows_by_units: dict[str, list[Row]] = field(default_factory=lambda: {})
    full_refresh_date: date_ | None = None
    fetched: bool = False
    failure: str | None = None


def _summary(run_date: date_, status: RunStatus, rows: int, detail: str) -> JobRun:
    return journal_run(JOB_NAME, run_date, rows_written=rows, status=status, detail=detail)


def _payload_list(result: SourceResult, key: str) -> list[Row]:
    raw = result.payload.get(key)
    return cast("list[Row]", raw) if isinstance(raw, list) else []


def _payload_dict(result: SourceResult, key: str) -> Row | None:
    raw = result.payload.get(key)
    return cast("Row", raw) if isinstance(raw, dict) else None


def _date_of(raw: object) -> date_ | None:
    if isinstance(raw, date_):
        return raw
    if isinstance(raw, str):
        try:
            return date_.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _group_previous_observations(rows: list[Row]) -> dict[tuple[str, str], list[Row]]:
    grouped: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        grouped.setdefault((str(row["series_id"]), str(row["units"])), []).append(row)
    return grouped


def _observation_rows(series_id: str, units: str, result: SourceResult) -> list[Row]:
    rows: list[Row] = []
    for raw in _payload_list(result, "observations"):
        observed = _date_of(raw.get("date"))
        value = raw.get("value")
        real_start = _date_of(raw.get("realtime_start")) or observed
        real_end = _date_of(raw.get("realtime_end")) or real_start
        if (
            observed is None
            or not isinstance(value, float)
            or real_start is None
            or real_end is None
        ):
            continue  # a missing observation ("." upstream) is not stored
        try:
            row = MacroObservationRow(
                series_id=series_id,
                units=units,
                observation_date=observed,
                value=value,
                realtime_start=real_start,
                realtime_end=real_end,
            )
        except ValidationError:  # pragma: no cover - the fields above are already typed
            logger.warning("fred_observation_row_rejected", series_id=series_id)
            continue
        rows.append(row.model_dump())
    return rows


def _full_refresh_due(previous: MacroSeriesMetaRow | None, run_date: date_) -> bool:
    if previous is None:
        return True
    return (run_date - previous.full_refresh_date).days >= FULL_REFRESH_EVERY_DAYS


def _needs_fetch(  # noqa: PLR0913 - the run's picture of one series
    spec: MacroSeriesSpec,
    series: Row,
    previous: MacroSeriesMetaRow | None,
    previous_rows: dict[tuple[str, str], list[Row]],
    updates: dict[str, object],
    *,
    full: bool,
) -> bool:
    if full or previous is None:
        return True
    if any(not previous_rows.get((spec.series_id, u.value)) for u in spec.stored_units):
        return True
    stamp = series.get("last_updated")
    if stamp != previous.last_updated:
        return True
    feed_stamp = updates.get(spec.series_id)
    return feed_stamp is not None and feed_stamp != previous.last_updated


async def _refresh_series(  # noqa: PLR0913, PLR0917 - the run's whole picture of one series
    ctx: IngestContext,
    spec: MacroSeriesSpec,
    meta_result: SourceResult,
    previous: MacroSeriesMetaRow | None,
    previous_rows: dict[tuple[str, str], list[Row]],
    updates: dict[str, object],
    run_date: date_,
    gate: asyncio.Semaphore,
) -> _SeriesOutcome:
    outcome = _SeriesOutcome(
        spec=spec, full_refresh_date=previous.full_refresh_date if previous else None
    )
    carried = {u.value: previous_rows.get((spec.series_id, u.value), []) for u in spec.stored_units}
    outcome.rows_by_units = {units: list(rows) for units, rows in carried.items()}
    if meta_result.health.status is not HealthStatus.OK:
        outcome.failure = f"metadata: {meta_result.health.detail}"
        return outcome
    outcome.series = _payload_dict(meta_result, "series")
    outcome.release = _payload_dict(meta_result, "release")
    if outcome.series is None:
        outcome.failure = "metadata: empty"
        return outcome
    full = _full_refresh_due(previous, run_date)
    if not _needs_fetch(spec, outcome.series, previous, previous_rows, updates, full=full):
        return outcome

    cutoff = run_date - timedelta(days=365 * LOOKBACK_YEARS)
    gateway = ctx.fred
    if gateway is None:  # pragma: no cover - gated by the caller
        outcome.failure = NOT_CONFIGURED_DETAIL
        return outcome
    fetched: dict[str, list[Row]] = {}
    async with gate:
        for units in (u.value for u in spec.stored_units):
            stored = carried[units]
            start = cutoff
            if not full and stored:
                newest = max(d for d in (_date_of(r["observation_date"]) for r in stored) if d)
                start = max(cutoff, newest - timedelta(days=_OVERLAP_DAYS[spec.frequency]))
            result = await gateway.fetch_all_observations(
                spec.series_id,
                units=units,
                observation_start=start,
                observation_end=None,
                tls_policy=ctx.tls_policy,
            )
            if result.health.status not in (HealthStatus.OK, HealthStatus.STALE):
                outcome.failure = f"observations ({units}): {result.health.detail}"
                return outcome  # keep every stored row for this series; the run is PARTIAL
            new_rows = _observation_rows(spec.series_id, units, result)
            if not new_rows and stored:
                continue  # an empty answer never replaces stored rows
            kept = (
                []
                if full
                else [r for r in stored if (_date_of(r["observation_date"]) or start) < start]
            )
            fetched[units] = [
                r
                for r in [*kept, *new_rows]
                if (_date_of(r["observation_date"]) or cutoff) >= cutoff
            ]
    outcome.rows_by_units.update(fetched)
    outcome.fetched = True
    if full:
        outcome.full_refresh_date = run_date
    return outcome


async def _refresh_vintages(
    ctx: IngestContext,
    outcomes: dict[str, _SeriesOutcome],
    previous: dict[str, list[Row]],
    previous_vintage_dates: dict[str, date_ | None],
    gate: asyncio.Semaphore,
) -> tuple[dict[str, list[Row]], dict[str, date_ | None], list[str]]:
    gateway = ctx.fred
    vintages: dict[str, list[Row]] = {sid: list(rows) for sid, rows in previous.items()}
    latest: dict[str, date_ | None] = dict(previous_vintage_dates)
    failures: list[str] = []
    if gateway is None:  # pragma: no cover - gated by the caller
        return vintages, latest, failures

    async def one(spec: MacroSeriesSpec) -> None:
        outcome = outcomes.get(spec.series_id)
        if outcome is None or not (outcome.fetched or not previous.get(spec.series_id)):
            return
        stored = [row for rows in outcome.rows_by_units.values() for row in rows]
        newest = max(
            (d for d in (_date_of(r["observation_date"]) for r in stored) if d), default=None
        )
        if newest is None:
            return
        # An observation first appears on or after its own date, so the real-time range starts at
        # the window start: nothing earlier can be a first release, and the request stays under
        # the cap on vintage dates per call (a whole-history range would exceed it for the
        # long, often-revised series).
        start = newest - timedelta(days=_VINTAGE_WINDOW_DAYS.get(spec.frequency, 760))
        async with gate:
            result = await gateway.fetch_all_observations(
                spec.series_id,
                units="lin",
                observation_start=start,
                realtime_start=start,
                realtime_end=LATEST_REALTIME,
                tls_policy=ctx.tls_policy,
            )
            if result.health.status not in (HealthStatus.OK, HealthStatus.STALE):
                failures.append(f"{spec.series_id} vintages: {result.health.detail}")
                outcome.failure = outcome.failure or f"vintages: {result.health.detail}"
                return
            rows: list[Row] = []
            for raw in _payload_list(result, "observations"):
                observed = _date_of(raw.get("date"))
                real_start = _date_of(raw.get("realtime_start"))
                real_end = _date_of(raw.get("realtime_end"))
                value = raw.get("value")
                if observed is None or real_start is None or real_end is None:
                    continue
                if not isinstance(value, float):
                    continue
                rows.append(
                    MacroVintageRow(
                        series_id=spec.series_id,
                        observation_date=observed,
                        realtime_start=real_start,
                        realtime_end=real_end,
                        value=value,
                    ).model_dump()
                )
            if rows:  # an empty answer keeps the stored history
                vintages[spec.series_id] = rows
            dates = await gateway.vintage_dates(
                spec.series_id, sort_order="desc", limit=1, tls_policy=ctx.tls_policy
            )
            listed = _payload_list_of_str(dates, "vintage_dates")
            if listed:
                latest[spec.series_id] = _date_of(listed[0])

    await asyncio.gather(*(one(spec) for spec in REVISION_SERIES))
    return vintages, latest, failures


def _payload_list_of_str(result: SourceResult, key: str) -> list[str]:
    raw = result.payload.get(key)
    return (
        [item for item in cast("list[object]", raw) if isinstance(item, str)]
        if isinstance(raw, list)
        else []
    )


def _meta_row(
    outcome: _SeriesOutcome,
    previous: MacroSeriesMetaRow | None,
    run_date: date_,
    latest_vintage: date_ | None,
) -> Row | None:
    spec = outcome.spec
    if outcome.series is None:
        return previous.model_dump() if previous is not None else None
    series = outcome.series
    # A failed `series/release` lookup must not blank the release link a series already had.
    release = outcome.release or {}
    if outcome.release is None and previous is not None:
        release = {
            "id": previous.release_id,
            "name": previous.release_name,
            "link": previous.release_link,
        }
    # A failed observation or vintage read keeps the stamp from before: tomorrow's run sees it
    # changed again and retries, instead of waiting up to a week for the full refresh.
    stamp = str(series.get("last_updated") or "")
    if outcome.failure is not None and previous is not None:
        stamp = previous.last_updated
    default_rows = outcome.rows_by_units.get(spec.default_units.value, [])
    try:
        return MacroSeriesMetaRow(
            series_id=spec.series_id,
            title=str(series.get("title") or spec.label),
            units=str(series.get("units") or ""),
            units_short=str(series.get("units_short") or ""),
            frequency=str(series.get("frequency") or ""),
            frequency_short=str(series.get("frequency_short") or ""),
            seasonal_adjustment=str(series.get("seasonal_adjustment") or ""),
            seasonal_adjustment_short=str(series.get("seasonal_adjustment_short") or ""),
            last_updated=stamp,
            observation_start=_date_of(series.get("observation_start")),
            observation_end=_date_of(series.get("observation_end")),
            popularity=cast("int | None", series.get("popularity")),
            notes=cast("str | None", series.get("notes")),
            release_id=cast("int | None", release.get("id")),
            release_name=cast("str | None", release.get("name")),
            release_link=cast("str | None", release.get("link")),
            stored_observations=len(default_rows),
            full_refresh_date=outcome.full_refresh_date or run_date,
            latest_vintage_date=latest_vintage,
        ).model_dump()
    except ValidationError as exc:
        logger.warning("fred_meta_row_rejected", series_id=spec.series_id, error=str(exc))
        return previous.model_dump() if previous is not None else None


def _release_rows(
    result: SourceResult,
    meta_rows: list[Row],
    run_date: date_,
) -> list[Row]:
    names: dict[int, str] = {}
    for meta in meta_rows:
        release_id = meta.get("release_id")
        release_name = meta.get("release_name")
        if isinstance(release_id, int) and isinstance(release_name, str):
            names[release_id] = release_name
    low = run_date - timedelta(days=RELEASE_WINDOW_PAST_DAYS)
    high = run_date + timedelta(days=RELEASE_WINDOW_FUTURE_DAYS)
    seen: set[tuple[int, date_]] = set()
    rows: list[Row] = []
    for raw in _payload_list(result, "release_dates"):
        release_id = raw.get("release_id")
        day = _date_of(raw.get("date"))
        if not isinstance(release_id, int) or release_id not in names or day is None:
            continue
        if not low <= day <= high or (release_id, day) in seen:
            continue
        seen.add((release_id, day))
        rows.append(
            MacroReleaseDateRow(
                release_id=release_id, release_name=names[release_id], release_date=day
            ).model_dump()
        )
    return sorted(rows, key=lambda r: (str(r["release_date"]), str(r["release_name"])))


def _settle(ctx: IngestContext, dataset: str, run_date: date_, rows: list[Row]) -> list[Row] | None:
    """The rows to write, or `None` to leave the stored partition alone: an empty answer never
    replaces a day that already has rows."""
    if not rows and ctx.store.has_rows(dataset, MARKET, run_date):
        return None
    return rows


@dataclass
class _Previous:
    """What the store already holds, as of the newest partition on or before the run date."""

    observations: dict[tuple[str, str], list[Row]]
    meta: dict[str, MacroSeriesMetaRow]
    vintages: dict[str, list[Row]]
    has_observations: bool
    has_vintages: bool


def _load_previous(store: ParquetStore, run_date: date_) -> _Previous:
    def newest(dataset: str) -> list[Row] | None:
        found = store.latest_partition_on_or_before(dataset, MARKET, run_date)
        return store.read_partition(dataset, MARKET, found[0]) if found else None

    observations = newest(US_MACRO_OBSERVATIONS_DATASET)
    meta = newest(US_MACRO_META_DATASET)
    vintages = newest(US_MACRO_VINTAGES_DATASET)
    by_series: dict[str, list[Row]] = {}
    for row in vintages or []:
        by_series.setdefault(str(row["series_id"]), []).append(row)
    return _Previous(
        observations=_group_previous_observations(observations or []),
        meta={row.series_id: row for row in rows_from_store(MacroSeriesMetaRow, meta or [])},
        vintages=by_series,
        has_observations=observations is not None,
        has_vintages=vintages is not None,
    )


async def _write_datasets(  # noqa: PLR0913, PLR0917 - the run's results; one argument each
    ctx: IngestContext,
    run_date: date_,
    results: list[_SeriesOutcome],
    meta_rows: list[Row],
    vintages: dict[str, list[Row]],
    release_rows: list[Row] | None,
    previous: _Previous,
) -> int:
    """Write what changed; returns the rows written. Observations and vintages are full snapshots
    written only when some series was re-read (or none existed yet); metadata is written on every
    run so its date says when the data was last checked."""
    store = ctx.store
    changed = any(o.fetched for o in results)
    written = 0
    observation_rows = [r for o in results for rows in o.rows_by_units.values() for r in rows]
    if observation_rows and (changed or not previous.has_observations):
        settled = _settle(ctx, US_MACRO_OBSERVATIONS_DATASET, run_date, observation_rows)
        if settled is not None:
            written += store.write_partition(
                US_MACRO_OBSERVATIONS_DATASET, MARKET, run_date, settled
            )
            await ctx.data_update_publisher.publish_data_updated(
                capability=Capability.US_MACRO_OBSERVATIONS,
                market=Market.US,
                as_of=run_date.isoformat(),
                scope="whole-market",
            )
    settled_meta = _settle(ctx, US_MACRO_META_DATASET, run_date, meta_rows)
    if settled_meta is not None:
        written += store.write_partition(US_MACRO_META_DATASET, MARKET, run_date, settled_meta)
    vintage_rows = [r for sid in sorted(vintages) for r in vintages[sid]]
    if vintage_rows and (changed or not previous.has_vintages):
        settled_vintages = _settle(ctx, US_MACRO_VINTAGES_DATASET, run_date, vintage_rows)
        if settled_vintages is not None:
            written += store.write_partition(
                US_MACRO_VINTAGES_DATASET, MARKET, run_date, settled_vintages
            )
    if release_rows is not None:
        settled_releases = _settle(ctx, US_MACRO_RELEASE_DATES_DATASET, run_date, release_rows)
        if settled_releases is not None:
            written += store.write_partition(
                US_MACRO_RELEASE_DATES_DATASET, MARKET, run_date, settled_releases
            )
    return written


def _final_run(
    run_date: date_, results: list[_SeriesOutcome], failures: list[str], written: int
) -> JobRun:
    refreshed = sum(1 for o in results if o.fetched)
    if not failures:
        return _summary(
            run_date, RunStatus.SUCCESS, written, f"{refreshed} of {len(results)} series refreshed"
        )
    status = RunStatus.PARTIAL if len(failures) < len(results) else RunStatus.FAILED
    shown = "; ".join(failures[:_FAILURES_SHOWN])
    extra = len(failures) - _FAILURES_SHOWN
    if extra > 0:
        shown += f"; +{extra} more"
    return _summary(run_date, status, written, f"{refreshed} series refreshed; {shown}")


async def job_fred_macro(run_date: date_, ctx: IngestContext) -> JobRun:
    """Pull the catalog's US macro series into the four Parquet datasets (module docstring)."""
    if not ctx.leader.is_leader():
        return _summary(run_date, RunStatus.SKIPPED, 0, "not the leader - write skipped")
    gateway = ctx.fred
    if gateway is None or not gateway.is_configured:
        return _summary(run_date, RunStatus.SKIPPED, 0, NOT_CONFIGURED_DETAIL)

    previous = _load_previous(ctx.store, run_date)
    metas = await gateway.series_metas(
        [spec.series_id for spec in SERIES_CATALOG], tls_policy=ctx.tls_policy
    )
    if not any(r.health.status is HealthStatus.OK for r in metas.values()):
        detail = next(iter(metas.values())).health.detail if metas else "no series requested"
        return _summary(run_date, RunStatus.FAILED, 0, f"no series metadata available: {detail}")

    feed = await gateway.recent_series_updates(tls_policy=ctx.tls_policy)
    raw_updates = feed.payload.get("series")
    updates = cast("dict[str, object]", raw_updates) if isinstance(raw_updates, dict) else {}

    gate = asyncio.Semaphore(_SERIES_CONCURRENCY)
    results = list(
        await asyncio.gather(
            *(
                _refresh_series(
                    ctx,
                    spec,
                    metas[spec.series_id],
                    previous.meta.get(spec.series_id),
                    previous.observations,
                    updates,
                    run_date,
                    gate,
                )
                for spec in SERIES_CATALOG
            )
        )
    )
    failures = [f"{o.spec.series_id} {o.failure}" for o in results if o.failure]

    vintages, latest_vintage, vintage_failures = await _refresh_vintages(
        ctx,
        {o.spec.series_id: o for o in results},
        previous.vintages,
        {sid: row.latest_vintage_date for sid, row in previous.meta.items()},
        gate,
    )
    failures.extend(vintage_failures)

    meta_rows: list[Row] = []
    for outcome in results:
        series_id = outcome.spec.series_id
        row = _meta_row(
            outcome, previous.meta.get(series_id), run_date, latest_vintage.get(series_id)
        )
        if row is not None:
            meta_rows.append(row)

    release_result = await gateway.release_dates(
        from_date=run_date - timedelta(days=RELEASE_WINDOW_PAST_DAYS),
        to_date=run_date + timedelta(days=RELEASE_WINDOW_FUTURE_DAYS),
        include_future=True,
        tls_policy=ctx.tls_policy,
    )
    release_rows: list[Row] | None = None
    if release_result.health.status in (HealthStatus.OK, HealthStatus.STALE):
        release_rows = _release_rows(release_result, meta_rows, run_date)
    else:
        failures.append(f"release dates: {release_result.health.detail}")

    written = await _write_datasets(
        ctx, run_date, results, meta_rows, vintages, release_rows, previous
    )
    return _final_run(run_date, results, failures, written)


__all__ = [
    "FULL_REFRESH_EVERY_DAYS",
    "JOB_NAME",
    "RELEASE_WINDOW_FUTURE_DAYS",
    "RELEASE_WINDOW_PAST_DAYS",
    "job_fred_macro",
]
