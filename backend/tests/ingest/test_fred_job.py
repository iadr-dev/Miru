"""`job_fred_macro` - the US macro statistics ingest job, driven against a fake service over a mock
HTTP transport (no network, no key) and a real `ParquetStore` in `tmp_path`."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ingest.fred_store import (
    MARKET,
    US_MACRO_META_DATASET,
    US_MACRO_OBSERVATIONS_DATASET,
    US_MACRO_RELEASE_DATES_DATASET,
    US_MACRO_VINTAGES_DATASET,
    read_meta,
    read_observations,
    read_release_dates,
    read_vintages,
)
from ingest.jobs import job_fred_macro
from ingest.journal import RunStatus
from ingest.leader import NeverLeader
from ingest.store import ParquetStore
from schemas.fred_catalog import (
    CATALOG_BY_ID,
    REVISION_SERIES,
    SERIES_CATALOG,
    MacroUnits,
)
from sources.fred import NOT_CONFIGURED_DETAIL
from tests.fred_support import (
    ANCHOR,
    RUN_DATE,
    FakeFredServer,
    install,
    make_ctx,
    run_job,
    series_dates,
)


async def test_without_a_key_the_job_journals_skipped_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    install(monkeypatch, server)

    run = await job_fred_macro(RUN_DATE, make_ctx(tmp_path, key=None))

    assert run.status is RunStatus.SKIPPED
    assert run.detail == NOT_CONFIGURED_DETAIL
    assert run.rows_written == 0
    assert not server.requests
    assert ParquetStore(tmp_path).list_partition_dates(US_MACRO_OBSERVATIONS_DATASET, MARKET) == ()


async def test_a_context_without_the_source_skips_too(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    object.__setattr__(ctx, "fred", None)

    run = await job_fred_macro(RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED


async def test_a_non_leader_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeFredServer()
    install(monkeypatch, server)

    run = await job_fred_macro(RUN_DATE, make_ctx(tmp_path, leader=NeverLeader()))

    assert run.status is RunStatus.SKIPPED
    assert "leader" in run.detail
    assert not server.requests


async def test_the_first_run_writes_all_four_datasets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, server = await run_job(tmp_path, monkeypatch)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == "45 of 45 series refreshed"
    store = ParquetStore(tmp_path)
    for dataset in (
        US_MACRO_OBSERVATIONS_DATASET,
        US_MACRO_META_DATASET,
        US_MACRO_RELEASE_DATES_DATASET,
        US_MACRO_VINTAGES_DATASET,
    ):
        assert store.list_partition_dates(dataset, MARKET) == (RUN_DATE,)

    observations = read_observations(store)
    # every stored transform of every series is there
    expected = {(s.series_id, u.value) for s in SERIES_CATALOG for u in s.stored_units}
    assert set(observations) == expected
    # missing ('.') observations are not stored
    dgs10 = observations[("DGS10", "lin")]
    assert len(dgs10) < len(series_dates(CATALOG_BY_ID["DGS10"]))
    assert dgs10[-1].observation_date == ANCHOR
    # transformations were requested from the service, not computed here
    cpi_units = {r.url.params.get("units") for r in server.observation_requests("CPIAUCSL")}
    assert cpi_units == {"pc1", "pch", "lin"}

    as_of, meta = read_meta(store)
    assert as_of == RUN_DATE
    assert set(meta) == {s.series_id for s in SERIES_CATALOG}
    gdp = meta["GDPC1"]
    assert gdp.release_id == 53
    assert gdp.release_name == "Gross Domestic Product"
    assert gdp.stored_observations == len(observations[("GDPC1", "lin")])
    assert gdp.full_refresh_date == RUN_DATE
    assert gdp.latest_vintage_date == date(2026, 10, 2)  # first of the 'desc' vintage dates
    assert meta["DGS10"].latest_vintage_date is None  # not revision-tracked


async def test_release_dates_are_filtered_to_the_catalog_and_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_job(tmp_path, monkeypatch)

    releases = read_release_dates(ParquetStore(tmp_path))

    names = {r.release_name for r in releases}
    assert "Not in the catalog" not in names
    assert {"Employment Situation", "Consumer Price Index", "Gross Domestic Product"} <= names
    assert all(
        RUN_DATE - timedelta(days=45) <= r.release_date <= RUN_DATE + timedelta(days=150)
        for r in releases
    )
    jobs = [r for r in releases if r.release_name == "Employment Situation"]
    assert len(jobs) == 2  # one recent, one upcoming
    assert releases == sorted(releases, key=lambda r: (r.release_date, r.release_name))


async def test_revision_tracked_series_store_their_vintage_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    latest_month = date(2026, 10, 1)
    server.revisions["PAYEMS"] = {
        latest_month: [(date(2026, 11, 6), 100.0), (date(2026, 12, 4), 112.0)],
    }

    await run_job(tmp_path, monkeypatch, server)

    vintages = read_vintages(ParquetStore(tmp_path), "PAYEMS")
    history = sorted(
        (v for v in vintages if v.observation_date == latest_month), key=lambda v: v.realtime_start
    )
    assert [v.value for v in history] == [100.0, 112.0]
    assert history[0].realtime_end == date(2026, 12, 3)
    assert history[1].realtime_end == date(9999, 12, 31)
    # only the revision-tracked series have a vintage history
    stored_ids = {
        v.series_id
        for sid in (s.series_id for s in REVISION_SERIES)
        for v in read_vintages(ParquetStore(tmp_path), sid)
    }
    assert stored_ids == {s.series_id for s in REVISION_SERIES}
    assert read_vintages(ParquetStore(tmp_path), "DGS10") == []


async def test_rerunning_the_same_day_changes_nothing_and_refetches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    before = store.partition_files(US_MACRO_OBSERVATIONS_DATASET, MARKET, RUN_DATE)
    calls_before = len(server.observation_requests())

    run = await job_fred_macro(RUN_DATE, make_ctx(tmp_path))

    assert run.status is RunStatus.SUCCESS
    assert run.detail == "0 of 45 series refreshed"
    assert len(server.observation_requests()) == calls_before
    assert store.partition_files(US_MACRO_OBSERVATIONS_DATASET, MARKET, RUN_DATE) == before


async def test_next_day_only_the_changed_series_is_reread_over_a_recent_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    server.requests.clear()
    server.last_updated["UNRATE"] = "2026-10-15 08:00:00-05"
    revised_month = date(2026, 9, 1)
    server.overrides[("UNRATE", "lin", revised_month)] = 9.99
    next_day = RUN_DATE + timedelta(days=1)

    run = await job_fred_macro(next_day, make_ctx(tmp_path))

    assert run.status is RunStatus.SUCCESS
    assert run.detail == "1 of 45 series refreshed"
    fetched = {r.url.params["series_id"] for r in server.observation_requests()}
    assert fetched == {"UNRATE"}
    window_start = date.fromisoformat(
        server.observation_requests("UNRATE")[0].url.params["observation_start"]
    )
    assert window_start > next_day - timedelta(days=5 * 365)  # recent window, not the full history
    store = ParquetStore(tmp_path)
    assert store.list_partition_dates(US_MACRO_OBSERVATIONS_DATASET, MARKET) == (RUN_DATE, next_day)
    points = {
        p.observation_date: p.value
        for p in read_observations(store, [("UNRATE", "lin")])[("UNRATE", "lin")]
    }
    assert points[revised_month] == 9.99  # the revision is visible in the newest snapshot
    assert len(points) == len(series_dates(CATALOG_BY_ID["UNRATE"]))  # older points kept
    # the previous snapshot is still there as the trail
    earlier = ParquetStore(tmp_path).read_partition(US_MACRO_OBSERVATIONS_DATASET, MARKET, RUN_DATE)
    old = next(
        r
        for r in earlier
        if r["series_id"] == "UNRATE"
        and r["units"] == "lin"
        and r["observation_date"] == revised_month
    )
    assert old["value"] != 9.99
    # metadata is refreshed every run, observations only when something changed
    assert store.list_partition_dates(US_MACRO_META_DATASET, MARKET) == (RUN_DATE, next_day)


async def test_a_day_with_no_change_writes_no_new_observation_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    next_day = RUN_DATE + timedelta(days=1)

    run = await job_fred_macro(next_day, make_ctx(tmp_path))

    store = ParquetStore(tmp_path)
    assert run.status is RunStatus.SUCCESS
    assert store.list_partition_dates(US_MACRO_OBSERVATIONS_DATASET, MARKET) == (RUN_DATE,)
    assert store.list_partition_dates(US_MACRO_META_DATASET, MARKET) == (RUN_DATE, next_day)
    as_of, _ = read_meta(store)
    assert as_of == next_day  # "last checked" still moves forward


async def test_the_weekly_refresh_rereads_the_whole_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    server.requests.clear()
    a_week_later = RUN_DATE + timedelta(days=7)

    run = await job_fred_macro(a_week_later, make_ctx(tmp_path))

    assert run.detail == "45 of 45 series refreshed"
    starts = {
        date.fromisoformat(r.url.params["observation_start"]) for r in server.observation_requests()
    }
    assert starts == {a_week_later - timedelta(days=5 * 365)}
    _, meta = read_meta(ParquetStore(tmp_path))
    assert all(m.full_refresh_date == a_week_later for m in meta.values())


async def test_a_failing_series_keeps_its_stored_rows_and_makes_the_run_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    before = read_observations(store, [("ICSA", "lin")])[("ICSA", "lin")]
    server.last_updated["ICSA"] = "2026-10-15 08:00:00-05"
    server.last_updated["CCSA"] = "2026-10-15 08:00:00-05"
    server.failing_observations.add("ICSA")
    next_day = RUN_DATE + timedelta(days=1)

    run = await job_fred_macro(next_day, make_ctx(tmp_path))

    assert run.status is RunStatus.PARTIAL
    assert "ICSA" in run.detail
    after = read_observations(store, [("ICSA", "lin"), ("CCSA", "lin")])
    assert after[("ICSA", "lin")] == before  # kept as it was
    assert len(after[("CCSA", "lin")]) == len(series_dates(CATALOG_BY_ID["CCSA"]))  # others proceed


async def test_a_failed_observation_read_is_retried_the_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A series whose observation read fails must keep its old `last_updated` stamp, so the next
    run sees the stamp changed again and re-reads it, instead of waiting for the weekly refresh."""
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    server.last_updated["UNRATE"] = "2026-10-15 08:00:00-05"
    server.failing_observations.add("UNRATE")
    day_two = RUN_DATE + timedelta(days=1)
    assert (await job_fred_macro(day_two, make_ctx(tmp_path))).status is RunStatus.PARTIAL
    _, meta = read_meta(store)
    assert meta["UNRATE"].last_updated != "2026-10-15 08:00:00-05"  # not recorded as seen

    server.failing_observations.clear()
    server.requests.clear()
    run = await job_fred_macro(day_two + timedelta(days=1), make_ctx(tmp_path))

    assert run.status is RunStatus.SUCCESS
    assert {r.url.params["series_id"] for r in server.observation_requests()} == {"UNRATE"}
    _, meta = read_meta(store)
    assert meta["UNRATE"].last_updated == "2026-10-15 08:00:00-05"


async def test_a_failed_release_lookup_keeps_the_series_release_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    _, before = read_meta(store)
    assert before["CPIAUCSL"].release_name
    server.failing_release.add("CPIAUCSL")

    await job_fred_macro(RUN_DATE + timedelta(days=1), make_ctx(tmp_path))

    _, after = read_meta(store)
    assert after["CPIAUCSL"].release_id == before["CPIAUCSL"].release_id
    assert after["CPIAUCSL"].release_name == before["CPIAUCSL"].release_name


async def test_vintage_history_is_requested_from_the_window_start_not_from_1776(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-history real-time range exceeds FRED's cap on vintage dates for the long,
    often-revised series; an observation is never first published before its own date."""
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)

    vintage_requests = [
        r
        for r in server.requests
        if r.url.path.endswith("/series/observations")
        and "realtime_end" in r.url.params
        and r.url.params["series_id"] == "PAYEMS"
    ]
    assert vintage_requests
    assert all(r.url.params["realtime_start"] > "1990-01-01" for r in vintage_requests)


async def test_a_failing_metadata_lookup_carries_the_stored_series_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    server.failing_meta.add("DGS2")
    next_day = RUN_DATE + timedelta(days=1)

    run = await job_fred_macro(next_day, make_ctx(tmp_path))

    assert run.status is RunStatus.PARTIAL
    _, meta = read_meta(ParquetStore(tmp_path))
    assert meta["DGS2"].title == "Title of DGS2"  # carried from the earlier snapshot
    assert read_observations(ParquetStore(tmp_path), [("DGS2", "lin")])[("DGS2", "lin")]


async def test_an_empty_answer_never_replaces_stored_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    before = read_observations(store, [("UNRATE", "lin")])[("UNRATE", "lin")]
    server.last_updated["UNRATE"] = "2026-10-15 08:00:00-05"
    server.empty_observations.add("UNRATE")

    run = await job_fred_macro(RUN_DATE + timedelta(days=1), make_ctx(tmp_path))

    assert run.status is RunStatus.SUCCESS
    assert read_observations(store, [("UNRATE", "lin")])[("UNRATE", "lin")] == before


async def test_an_empty_first_pull_writes_no_observation_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    server.empty_observations.update(s.series_id for s in SERIES_CATALOG)

    run, _ = await run_job(tmp_path, monkeypatch, server)

    store = ParquetStore(tmp_path)
    assert store.list_partition_dates(US_MACRO_OBSERVATIONS_DATASET, MARKET) == ()
    assert run.status is RunStatus.SUCCESS
    assert store.list_partition_dates(US_MACRO_META_DATASET, MARKET) == (RUN_DATE,)


async def test_when_every_metadata_lookup_fails_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeFredServer()
    server.all_meta_fail = True

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.FAILED
    assert "no series metadata" in run.detail
    store = ParquetStore(tmp_path)
    assert store.list_partition_dates(US_MACRO_META_DATASET, MARKET) == ()


async def test_stored_default_units_match_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_job(tmp_path, monkeypatch)

    observations = read_observations(ParquetStore(tmp_path))

    payems = observations[("PAYEMS", MacroUnits.CHANGE.value)]
    assert payems[0].value > 100  # the 'chg' transform, as requested from the service
    assert ("PAYEMS", "pc1") not in observations
