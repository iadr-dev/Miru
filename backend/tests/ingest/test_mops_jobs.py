"""`job_material_announcements`/`job_insider_transfer_declaration` — the two MOPS whole-market
disclosure-feed daily ingest jobs. Not FinMind-sourced (same `job_etf_nav`-shaped carve-out —
see `test_etf_nav_job.py` for the template this mirrors), so their own dedicated whole-market
fetch functions (`sources.mops.fetch_whole_market_material_announcements`/
`fetch_whole_market_insider_transfer_declarations`) are mocked directly.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

import ingest.jobs.disclosures as jobs_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import (
    INSIDER_TRANSFER_DECLARATION_DATASET,
    MATERIAL_ANNOUNCEMENTS_DATASET,
    job_insider_transfer_declaration,
    job_material_announcements,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.finmind import FinMindAdapter

_RUN_DATE = date(2026, 8, 28)
_ANNOUNCEMENT_ROW = {
    "symbol": "2330",
    "market": "TW",
    "company_name": "台積電",
    "announced_date": "2026-08-28",
    "announced_time": "09:11:07",
    "subject": "公告本公司將於115年09月11日召開第1次法人說明會",
    "detail": "法說會相關說明",
    "qualifying_clause": "第12款",
    "category": "法說會",
}
_INSIDER_TRANSFER_ROW = {
    "symbol": "2884",
    "market": "TW",
    "company_name": "玉山金",
    "filer_role": "子公司經理人本人",
    "person_name": "鄭恩融",
    "transfer_method": "一般交易(每日得轉讓股數限制)",
    "transfer_shares": 220000,
    "max_daily_exchange_shares": 17753510,
    "transferee": None,
    "current_shares_own": 546279,
    "current_shares_trust": 0,
    "planned_shares_own": 220000,
    "planned_shares_trust": 0,
    "projected_shares_own_after": 326279,
    "projected_shares_trust_after": 0,
    "transfer_window_start": "2026-09-06",
    "transfer_window_end": "2026-10-05",
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


def _fake_result(*, rows: list[dict[str, object]], status: HealthStatus) -> SourceResult:
    return SourceResult(
        source_id="MOPS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat="test caveat",
        payload={"rows": rows},
        health=SourceHealth(status=status, detail="ok" if status is HealthStatus.OK else "fail"),
    )


async def test_material_announcements_success_writes_partition(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ANNOUNCEMENT_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_material_announcements(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
    assert ctx.store.has_partition(MATERIAL_ANNOUNCEMENTS_DATASET, Market.TW.value, _RUN_DATE)


async def test_material_announcements_malformed_row_is_dropped(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_row = {"symbol": "2330"}  # missing every other required field

    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ANNOUNCEMENT_ROW, bad_row], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_material_announcements(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1


async def test_material_announcements_transport_failure_is_failed(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[], status=HealthStatus.UNREACHABLE)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_material_announcements(_RUN_DATE, ctx)

    assert run.status is RunStatus.FAILED
    assert not ctx.store.has_partition(MATERIAL_ANNOUNCEMENTS_DATASET, Market.TW.value, _RUN_DATE)


async def test_material_announcements_not_leader_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch when not the leader")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_material_announcements(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_material_announcements_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch on a non-trading day")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2026, 8, 29)

    run = await job_material_announcements(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_material_announcements_idempotent_rerun_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ANNOUNCEMENT_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_material_announcements", _fake_fetch)
    ctx = _ctx(tmp_path)

    first = await job_material_announcements(_RUN_DATE, ctx)
    second = await job_material_announcements(_RUN_DATE, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 1


async def test_insider_transfer_declaration_success_writes_partition(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_INSIDER_TRANSFER_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(
        jobs_module, "fetch_whole_market_insider_transfer_declarations", _fake_fetch
    )
    ctx = _ctx(tmp_path)

    run = await job_insider_transfer_declaration(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
    assert ctx.store.has_partition(INSIDER_TRANSFER_DECLARATION_DATASET, Market.TW.value, _RUN_DATE)


async def test_insider_transfer_declaration_malformed_row_is_dropped(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_row = {"symbol": "2884"}  # missing every other required field

    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_INSIDER_TRANSFER_ROW, bad_row], status=HealthStatus.OK)

    monkeypatch.setattr(
        jobs_module, "fetch_whole_market_insider_transfer_declarations", _fake_fetch
    )
    ctx = _ctx(tmp_path)

    run = await job_insider_transfer_declaration(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1


async def test_insider_transfer_declaration_not_leader_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch when not the leader")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_insider_transfer_declarations", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_insider_transfer_declaration(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_insider_transfer_declaration_idempotent_rerun_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_INSIDER_TRANSFER_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(
        jobs_module, "fetch_whole_market_insider_transfer_declarations", _fake_fetch
    )
    ctx = _ctx(tmp_path)

    first = await job_insider_transfer_declaration(_RUN_DATE, ctx)
    second = await job_insider_transfer_declaration(_RUN_DATE, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 1
