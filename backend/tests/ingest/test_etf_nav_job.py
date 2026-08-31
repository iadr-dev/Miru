"""`job_etf_nav` — the ETF premium/discount-to-NAV daily ingest job. Not FinMind-sourced (unlike
every other job in `ingest/jobs.py`), so its own dedicated whole-market fetch function
(`sources.twse_etf_nav.fetch_whole_market_etf_nav`) is mocked directly rather than the shared
FinMind test client factory.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

import ingest.jobs as jobs_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import ETF_NAV_DATASET, job_etf_nav
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.finmind import FinMindAdapter

_RUN_DATE = date(2026, 8, 28)
_ROW = {
    "symbol": "0050",
    "market": "TW",
    "as_of": _RUN_DATE.isoformat(),
    "fund_name": "元大台灣50",
    "units_outstanding": 22_356_000_000,
    "units_outstanding_change": -4_500_000,
    "close_price": 106.95,
    "estimated_nav": 107.21,
    "premium_discount_pct": -0.24,
    "prior_day_nav": 106.14,
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
        source_id="TWSE_MIS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="test caveat",
        payload={"rows": rows},
        health=SourceHealth(status=status, detail="ok" if status is HealthStatus.OK else "fail"),
    )


async def test_success_writes_partition_and_validates_rows(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_etf_nav(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
    assert ctx.store.has_partition(ETF_NAV_DATASET, Market.TW.value, _RUN_DATE)


async def test_malformed_row_is_dropped_not_persisted(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row missing a required `EtfNavRow` field must be validated out before it ever reaches
    `write_partition` — the same ingest chokepoint every other job's `_validate_rows` enforces."""
    bad_row = {"symbol": "0050"}  # missing market/as_of/fund_name

    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ROW, bad_row], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_etf_nav(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1  # only the valid row survives


async def test_transport_failure_is_failed_not_success(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[], status=HealthStatus.UNREACHABLE)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _fake_fetch)
    ctx = _ctx(tmp_path)

    run = await job_etf_nav(_RUN_DATE, ctx)

    assert run.status is RunStatus.FAILED
    assert not ctx.store.has_partition(ETF_NAV_DATASET, Market.TW.value, _RUN_DATE)


async def test_not_leader_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch when not the leader")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_etf_nav(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*, tls_policy: object) -> SourceResult:
        raise AssertionError("must not fetch on a non-trading day")

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2026, 8, 29)

    run = await job_etf_nav(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_idempotent_rerun_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_fetch(*, tls_policy: object) -> SourceResult:
        return _fake_result(rows=[_ROW], status=HealthStatus.OK)

    monkeypatch.setattr(jobs_module, "fetch_whole_market_etf_nav", _fake_fetch)
    ctx = _ctx(tmp_path)

    first = await job_etf_nav(_RUN_DATE, ctx)
    second = await job_etf_nav(_RUN_DATE, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 1
