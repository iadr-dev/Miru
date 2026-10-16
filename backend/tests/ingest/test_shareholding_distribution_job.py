"""`job_shareholding_distribution` — the one whole-market ingest job whose upstream `start_date`
must be the EXACT weekly publish date (confirmed live: any other day in that week returns zero
rows), so the job itself searches backward from `run_date` rather than trusting it directly. See
that job's own docstring in `ingest/jobs.py`.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import httpx
import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import SHAREHOLDING_DISTRIBUTION_JOB, job_shareholding_distribution
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

_PUBLISH_DATE = date(2026, 8, 21)  # a Friday — the week's real publish date
_ROW = {
    "date": _PUBLISH_DATE.isoformat(),
    "stock_id": "2330",
    "HoldingSharesLevel": "1,000,001以上",
    "people": 1479,
    "percent": 84.71,
    "unit": 21_968_307_511,
}


def _settings() -> Settings:
    return Settings(finmind_api_key="test-token")


def _ctx(tmp_path: object, *, leader: object = None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=_settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _patch_date_conditional_client(monkeypatch: pytest.MonkeyPatch, *, publish_date: date) -> None:
    """FinMind's real upstream only returns rows for the exact matching `start_date` — every
    other date in `_call`'s query returns an empty `data` list. This mock reproduces that
    exactly, so the job's own backward-search logic is exercised for real, not bypassed by a
    fixture that answers the same body regardless of the requested date."""

    def handler(request: httpx.Request) -> httpx.Response:
        query_date = httpx.QueryParams(request.url.query.decode()).get("start_date")
        if query_date == publish_date.isoformat():
            return httpx.Response(200, json={"status": 200, "msg": "success", "data": [_ROW]})
        return httpx.Response(200, json={"status": 200, "msg": "success", "data": []})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)


async def test_resolves_the_real_publish_date_when_run_date_is_later_in_the_week(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon always calls this job with `today`, not necessarily the Friday the dataset
    actually published on — the job must search backward and write the partition under the
    resolved date, not `run_date`."""
    _patch_date_conditional_client(monkeypatch, publish_date=_PUBLISH_DATE)
    ctx = _ctx(tmp_path)
    called_as_of = _PUBLISH_DATE + __import__("datetime").timedelta(days=3)  # the next Monday

    run = await job_shareholding_distribution(called_as_of, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.run_date == _PUBLISH_DATE
    assert run.rows_written == 1
    assert ctx.store.has_partition(SHAREHOLDING_DISTRIBUTION_JOB.dataset, "TW", _PUBLISH_DATE)
    assert not ctx.store.has_partition(SHAREHOLDING_DISTRIBUTION_JOB.dataset, "TW", called_as_of)


async def test_gives_up_and_skips_when_no_publish_found_within_the_search_window(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every candidate date in the search window returns zero rows — a genuine gap (e.g. the
    daemon was down for the whole search window) — reported as SKIPPED, not a fabricated
    partition."""

    def always_empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 200, "msg": "success", "data": []})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(always_empty))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    ctx = _ctx(tmp_path)

    run = await job_shareholding_distribution(date(2026, 8, 21), ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_not_leader_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client when not the leader")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_shareholding_distribution(_PUBLISH_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_idempotent_rerun_same_publish_date_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_date_conditional_client(monkeypatch, publish_date=_PUBLISH_DATE)
    ctx = _ctx(tmp_path)

    first = await job_shareholding_distribution(_PUBLISH_DATE, ctx)
    second = await job_shareholding_distribution(_PUBLISH_DATE, ctx)

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 1


@pytest.mark.parametrize("weekday_offset", [3, 4, 5, 6])
async def test_finds_the_fridays_publish_from_a_wednesday_to_a_thursday_run(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, weekday_offset: int
) -> None:
    """The weekly job and the weekly-stepping backfill can run on any weekday. From Tuesday on,
    Friday is 4-6 days back; a 5-day search stopped short of it on a Wednesday or Thursday, so the
    week was skipped every time (SKIPPED, never a failure)."""
    from datetime import timedelta  # noqa: PLC0415

    _patch_date_conditional_client(monkeypatch, publish_date=_PUBLISH_DATE)
    ctx = _ctx(tmp_path)

    run = await job_shareholding_distribution(_PUBLISH_DATE + timedelta(days=weekday_offset), ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.run_date == _PUBLISH_DATE
