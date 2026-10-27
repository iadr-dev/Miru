"""`job_disposition_events` — the 處置王-board disposition whole-market ingest job (the attention
job reads the exchanges' history reports instead: `test_attention_stocks_job.py`). Not
FinMind-sourced (same `job_etf_nav`-shaped carve-out — see `test_etf_nav_job.py` for the template
this mirrors); it calls BOTH a TWSE and a TPEX whole-market fetch function
(`sources.twse_openapi`/`sources.tpex_openapi`), concatenating their rows into one partition.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

import ingest.jobs.risk_lists as jobs_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import (
    DISPOSITION_EVENTS_DATASET,
    job_disposition_events,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.finmind import FinMindAdapter

_RUN_DATE = date(2026, 8, 28)
_DISPOSITION_ROW_TWSE = {
    "symbol": "3008",
    "market": "TW",
    "company_name": "大立光",
    "announced_date": "2026-09-02",
    "reason": "連續三次",
    "period_start": "2026-09-03",
    "period_end": "2026-09-09",
    "measure": "第一次處置",
    "detail": "處置原因：該有價證券之交易，連續三個營業日達本公司「公布注意交易資訊」標準。",
    "security_type": "twse",
}
_DISPOSITION_ROW_TPEX = {
    "symbol": "24552",
    "market": "TW",
    "company_name": "全新二",
    "announced_date": "2026-09-04",
    "reason": "轉(交)換公司債之標的證券經本中心或臺灣證券交易所發布處置",
    "period_start": "2026-09-07",
    "period_end": "2026-09-15",
    "measure": "上市公司全新光電科技股份有限公司股票經臺灣證券交易所發布第二次處置",
    "detail": None,
    "security_type": "tpex",
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
        source_id="TWSE_OPENAPI",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat="test caveat",
        payload={"rows": rows},
        health=SourceHealth(status=status, detail="ok" if status is HealthStatus.OK else "fail"),
    )


async def test_disposition_events_success_writes_partition_from_both_exchanges(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_twse(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TWSE], status=HealthStatus.OK)

    async def _fake_tpex(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TPEX], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _fake_twse)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _fake_tpex)
    ctx = _ctx(tmp_path)

    run = await job_disposition_events(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    assert ctx.store.has_partition(DISPOSITION_EVENTS_DATASET, Market.TW.value, _RUN_DATE)


async def test_disposition_events_malformed_row_is_dropped(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_row = {"symbol": "0000"}  # missing every other required field

    async def _fake_twse(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TWSE, bad_row], status=HealthStatus.OK)

    async def _fake_tpex(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _fake_twse)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _fake_tpex)
    ctx = _ctx(tmp_path)

    run = await job_disposition_events(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1


async def test_disposition_events_not_leader_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch when not the leader")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _boom)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_disposition_events(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_disposition_events_idempotent_rerun_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_twse(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TWSE], status=HealthStatus.OK)

    async def _fake_tpex(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TPEX], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _fake_twse)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _fake_tpex)
    ctx = _ctx(tmp_path)

    first = await job_disposition_events(_RUN_DATE, ctx)
    second = await job_disposition_events(_RUN_DATE, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 2


async def test_one_exchange_failing_keeps_its_earlier_rows_in_the_day(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A morning run stored both exchanges; an evening run where TPEx is down must not wipe the
    TPEx rows from the day (the 處置王 board reads only the latest partition)."""
    state = {"tpex_up": True}

    async def _fake_twse(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_DISPOSITION_ROW_TWSE], status=HealthStatus.OK)

    async def _fake_tpex(*, tls_policy: object) -> SourceResult:
        if state["tpex_up"]:
            return _fake_result(rows=[_DISPOSITION_ROW_TPEX], status=HealthStatus.OK)
        return _fake_result(rows=[], status=HealthStatus.UNREACHABLE)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _fake_twse)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _fake_tpex)
    ctx = _ctx(tmp_path)

    await job_disposition_events(_RUN_DATE, ctx)
    state["tpex_up"] = False
    run = await job_disposition_events(_RUN_DATE, ctx)

    assert run.status is RunStatus.PARTIAL
    stored = ctx.store.read_partition(DISPOSITION_EVENTS_DATASET, Market.TW.value, _RUN_DATE)
    assert sorted(r["security_type"] for r in stored) == ["tpex", "twse"]
    assert run.rows_written == 2


async def test_an_empty_answer_does_not_replace_a_non_empty_day(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"empty": False}

    async def _fake_twse(*, tls_policy: object) -> SourceResult:
        rows = [] if state["empty"] else [_DISPOSITION_ROW_TWSE]
        return _fake_result(rows=rows, status=HealthStatus.OK)

    async def _fake_tpex(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events", _fake_twse)
    monkeypatch.setattr(jobs_module, "fetch_whole_market_disposition_events_tpex", _fake_tpex)
    ctx = _ctx(tmp_path)

    await job_disposition_events(_RUN_DATE, ctx)
    state["empty"] = True
    run = await job_disposition_events(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    stored = ctx.store.read_partition(DISPOSITION_EVENTS_DATASET, Market.TW.value, _RUN_DATE)
    assert [r["symbol"] for r in stored] == ["3008"]
