"""`job_fred_macro` edge paths: the vintage refresh (failures, empty answers, when it is re-read),
the incremental observation window, first-run failures with nothing stored yet, and `_meta_row`
carried-forward rules. Driven through the same fake service as `test_fred_job`."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from ingest.fred_store import (
    MARKET,
    US_MACRO_RELEASE_DATES_DATASET,
    read_meta,
    read_observations,
    read_vintages,
)
from ingest.jobs import job_fred_macro
from ingest.jobs.fred import _meta_row, _SeriesOutcome  # pyright: ignore[reportPrivateUsage]
from ingest.journal import RunStatus
from ingest.store import ParquetStore
from schemas.capabilities import MacroSeriesMetaRow
from schemas.fred_catalog import CATALOG_BY_ID, SERIES_CATALOG
from tests.fred_support import ANCHOR, RUN_DATE, FakeFredServer, make_ctx, run_job


class _Server(FakeFredServer):
    """The shared fake plus knobs for the vintage and release-date reads."""

    def __init__(self) -> None:
        super().__init__()
        self.failing_vintages: set[str] = set()
        self.empty_vintages: set[str] = set()
        self.dirty_vintages: set[str] = set()
        self.failing_release_dates = False

    def _vintage_observations(self, spec, request):  # type: ignore[no-untyped-def]
        if spec.series_id in self.failing_vintages:
            return httpx.Response(500, text="boom")
        if spec.series_id in self.empty_vintages:
            return httpx.Response(200, json={"count": 0, "offset": 0, "observations": []})
        response = super()._vintage_observations(spec, request)
        if spec.series_id not in self.dirty_vintages:
            return response
        body = json.loads(response.content)
        body["observations"].append(
            {
                "realtime_start": "2026-10-01",
                "realtime_end": "9999-12-31",
                "date": ANCHOR.isoformat(),
                "value": ".",
            }
        )
        body["count"] = len(body["observations"])
        return httpx.Response(200, json=body)

    def _release_dates(self, request):  # type: ignore[no-untyped-def]
        if self.failing_release_dates:
            return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request."})
        return super()._release_dates(request)

    def vintage_requests(self, series_id: str | None = None) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.url.path.endswith("/series/observations")
            and "realtime_end" in r.url.params
            and (series_id is None or r.url.params["series_id"] == series_id)
        ]


NEXT_DAY = RUN_DATE + timedelta(days=1)
CHANGED = "2026-10-15 08:00:00-05"


async def test_a_failed_vintage_read_makes_the_run_partial_and_stores_no_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    server.failing_vintages.add("PAYEMS")

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.PARTIAL
    assert run.detail == "45 series refreshed; PAYEMS vintages: HTTP 500: Internal Server Error"
    store = ParquetStore(tmp_path)
    assert read_vintages(store, "PAYEMS") == []
    assert read_vintages(store, "CPIAUCSL")  # the other revision-tracked series are unaffected


async def test_a_failed_vintage_read_keeps_the_stored_history_and_the_old_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    before = read_vintages(store, "PAYEMS")
    _, meta_before = read_meta(store)
    server.last_updated["PAYEMS"] = CHANGED
    server.failing_vintages.add("PAYEMS")

    run = await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    assert run.status is RunStatus.PARTIAL
    assert read_vintages(store, "PAYEMS") == before
    _, meta = read_meta(store)
    assert meta["PAYEMS"].last_updated == meta_before["PAYEMS"].last_updated  # retried tomorrow


async def test_an_empty_vintage_answer_keeps_the_stored_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    store = ParquetStore(tmp_path)
    before = read_vintages(store, "PAYEMS")
    server.last_updated["PAYEMS"] = CHANGED
    server.empty_vintages.add("PAYEMS")

    run = await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    assert run.status is RunStatus.SUCCESS
    assert run.detail == "1 of 45 series refreshed"
    assert read_vintages(store, "PAYEMS") == before


async def test_a_day_with_no_change_reads_no_vintages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    assert server.vintage_requests()
    server.requests.clear()

    await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    assert server.vintage_requests() == []
    assert server.count("/series/vintagedates") == 0


async def test_only_a_changed_revision_series_reads_its_vintages_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    server.requests.clear()
    server.last_updated["PAYEMS"] = CHANGED
    server.last_updated["DGS10"] = CHANGED  # changed, but not revision-tracked

    await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    assert {r.url.params["series_id"] for r in server.vintage_requests()} == {"PAYEMS"}
    assert server.count("/series/vintagedates") == 1


async def test_a_revision_series_with_no_stored_history_is_requested_even_when_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    server.failing_vintages.add("PAYEMS")
    await run_job(tmp_path, monkeypatch, server)
    server.failing_vintages.clear()
    server.requests.clear()

    run = await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    assert {r.url.params["series_id"] for r in server.vintage_requests()} == {"PAYEMS"}
    # Read, but the vintage snapshot is only rewritten when some series was re-read (or none
    # existed yet), so nothing new is persisted on a day where nothing changed.
    assert read_vintages(ParquetStore(tmp_path), "PAYEMS") == []
    assert run.status is RunStatus.SUCCESS


async def test_vintage_rows_without_a_numeric_value_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_root = tmp_path / "clean"
    dirty_root = tmp_path / "dirty"
    await run_job(clean_root, monkeypatch, _Server())
    dirty = _Server()
    dirty.dirty_vintages.add("PAYEMS")
    await run_job(dirty_root, monkeypatch, dirty)

    clean_rows = read_vintages(ParquetStore(clean_root), "PAYEMS")
    dirty_rows = read_vintages(ParquetStore(dirty_root), "PAYEMS")

    assert clean_rows
    assert dirty_rows == clean_rows


async def test_the_vintage_window_start_follows_the_newest_stored_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)

    payems = server.vintage_requests("PAYEMS")
    assert len(payems) == 1
    # Monthly series: the newest observation is 2026-10-01, the window is 760 days.
    expected = (date(2026, 10, 1) - timedelta(days=760)).isoformat()
    assert payems[0].url.params["observation_start"] == expected
    assert payems[0].url.params["realtime_start"] == expected
    assert payems[0].url.params["realtime_end"] == "9999-12-31"
    assert payems[0].url.params["units"] == "lin"


async def test_a_changed_series_is_reread_from_the_newest_stored_point_minus_the_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    server.requests.clear()
    server.last_updated["UNRATE"] = CHANGED  # monthly: 400 days of overlap
    server.last_updated["DGS10"] = CHANGED  # daily: 45 days of overlap

    await job_fred_macro(NEXT_DAY, make_ctx(tmp_path))

    def start(series_id: str) -> str:
        return server.observation_requests(series_id)[0].url.params["observation_start"]

    assert start("UNRATE") == (date(2026, 10, 1) - timedelta(days=400)).isoformat()
    assert start("DGS10") == (ANCHOR - timedelta(days=45)).isoformat()


async def test_only_the_stored_rows_inside_the_cutoff_survive_a_window_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    await run_job(tmp_path, monkeypatch, server)
    server.last_updated["UNRATE"] = CHANGED
    server.overrides[("UNRATE", "lin", date(2026, 10, 1))] = 7.77
    # A day far enough ahead that the five-year cutoff drops the oldest stored points.
    far = date(2031, 6, 1)

    await job_fred_macro(far, make_ctx(tmp_path))

    points = read_observations(ParquetStore(tmp_path), [("UNRATE", "lin")])[("UNRATE", "lin")]
    cutoff = far - timedelta(days=365 * 5)
    assert points
    assert all(p.observation_date >= cutoff for p in points)


async def test_a_first_run_with_a_failing_metadata_lookup_stores_nothing_for_that_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    server.failing_meta.add("DGS2")

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.PARTIAL
    assert "DGS2 metadata:" in run.detail
    store = ParquetStore(tmp_path)
    _, meta = read_meta(store)
    assert "DGS2" not in meta
    assert len(meta) == len(SERIES_CATALOG) - 1
    assert ("DGS2", "lin") not in read_observations(store)


async def test_a_first_run_with_a_failing_release_lookup_has_no_release_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    server.failing_release.add("CPIAUCSL")

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.SUCCESS
    _, meta = read_meta(ParquetStore(tmp_path))
    assert meta["CPIAUCSL"].release_id is None
    assert meta["CPIAUCSL"].release_name is None
    assert meta["CPIAUCSL"].release_link is None
    assert meta["UNRATE"].release_id == 50


async def test_a_failed_release_dates_read_is_reported_and_writes_no_release_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    server.failing_release_dates = True

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.PARTIAL
    assert "release dates:" in run.detail
    store = ParquetStore(tmp_path)
    assert store.list_partition_dates(US_MACRO_RELEASE_DATES_DATASET, MARKET) == ()


async def test_more_than_five_failures_are_summarised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server()
    failing = [s.series_id for s in SERIES_CATALOG[:7]]
    server.failing_observations.update(failing)

    run, _ = await run_job(tmp_path, monkeypatch, server)

    assert run.status is RunStatus.PARTIAL
    assert run.detail.endswith("; +2 more")
    assert run.detail.startswith(f"{len(SERIES_CATALOG) - 7} series refreshed; ")


# ---- _meta_row ------------------------------------------------------------------------------

SPEC = CATALOG_BY_ID["UNRATE"]


def _previous(**overrides: object) -> MacroSeriesMetaRow:
    fields: dict[str, object] = {
        "series_id": "UNRATE",
        "title": "Old title",
        "units": "Percent",
        "units_short": "%",
        "frequency": "Monthly",
        "frequency_short": "M",
        "seasonal_adjustment": "Seasonally Adjusted",
        "seasonal_adjustment_short": "SA",
        "last_updated": "old-stamp",
        "observation_start": date(1948, 1, 1),
        "observation_end": date(2026, 9, 1),
        "popularity": 90,
        "notes": "old notes",
        "release_id": 50,
        "release_name": "Employment Situation",
        "release_link": "http://old",
        "stored_observations": 3,
        "full_refresh_date": date(2026, 10, 1),
        "latest_vintage_date": date(2026, 9, 5),
    }
    fields.update(overrides)
    return MacroSeriesMetaRow(**fields)  # type: ignore[arg-type]


def _outcome(**overrides: object) -> _SeriesOutcome:
    outcome = _SeriesOutcome(spec=SPEC)
    for key, value in overrides.items():
        setattr(outcome, key, value)
    return outcome


def test_meta_row_without_metadata_carries_the_previous_row_or_nothing() -> None:
    previous = _previous()
    assert _meta_row(_outcome(), None, RUN_DATE, None) is None
    assert _meta_row(_outcome(), previous, RUN_DATE, None) == previous.model_dump()


def test_meta_row_fills_blank_text_and_falls_back_to_the_catalog_label() -> None:
    row = _meta_row(_outcome(series={"last_updated": None}), None, RUN_DATE, date(2026, 10, 2))

    assert row is not None
    assert row["title"] == SPEC.label
    assert row["units"] == ""
    assert row["frequency"] == ""
    assert row["seasonal_adjustment_short"] == ""
    assert row["last_updated"] == ""
    assert row["release_id"] is None
    assert row["stored_observations"] == 0
    assert row["full_refresh_date"] == RUN_DATE
    assert row["latest_vintage_date"] == date(2026, 10, 2)


def test_meta_row_counts_the_default_units_rows_and_keeps_the_full_refresh_date() -> None:
    outcome = _outcome(
        series={
            "title": "Unemployment",
            "last_updated": "stamp",
            "observation_start": "1948-01-01",
        },
        release={"id": 50, "name": "Employment Situation", "link": "http://x"},
        rows_by_units={SPEC.default_units.value: [{"a": 1}, {"a": 2}], "other": [{"a": 3}]},
        full_refresh_date=date(2026, 10, 1),
    )

    row = _meta_row(outcome, None, RUN_DATE, None)

    assert row is not None
    assert row["title"] == "Unemployment"
    assert row["stored_observations"] == 2
    assert row["full_refresh_date"] == date(2026, 10, 1)
    assert row["observation_start"] == date(1948, 1, 1)
    assert row["release_id"] == 50
    assert row["release_link"] == "http://x"


def test_meta_row_keeps_the_previous_release_when_the_release_lookup_failed() -> None:
    previous = _previous()
    outcome = _outcome(series={"last_updated": "new-stamp"}, release=None)

    row = _meta_row(outcome, previous, RUN_DATE, None)

    assert row is not None
    assert row["release_id"] == 50
    assert row["release_name"] == "Employment Situation"
    assert row["release_link"] == "http://old"
    assert row["last_updated"] == "new-stamp"


def test_meta_row_prefers_an_answered_release_even_when_empty() -> None:
    previous = _previous()
    outcome = _outcome(series={"last_updated": "new-stamp"}, release={})

    row = _meta_row(outcome, previous, RUN_DATE, None)

    assert row is not None
    assert row["release_id"] is None
    assert row["last_updated"] == "new-stamp"


def test_meta_row_keeps_the_old_stamp_after_a_failure_with_a_previous_row() -> None:
    previous = _previous()
    failed = _outcome(series={"last_updated": "new-stamp"}, failure="observations (lin): boom")

    kept = _meta_row(failed, previous, RUN_DATE, None)
    first = _meta_row(failed, None, RUN_DATE, None)

    assert kept is not None
    assert kept["last_updated"] == "old-stamp"
    assert first is not None
    assert first["last_updated"] == "new-stamp"


def test_meta_row_rejected_by_validation_falls_back_to_the_previous_row() -> None:
    previous = _previous()
    bad = _outcome(series={"last_updated": "x", "popularity": "very popular"})

    assert _meta_row(bad, previous, RUN_DATE, None) == previous.model_dump()
    assert _meta_row(bad, None, RUN_DATE, None) is None
