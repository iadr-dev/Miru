"""Characterization (before the plan-gating change): a dataset outside the FinMind plan comes back
as HTTP 400, which the adapter reports as `UNREACHABLE` ("FinMind HTTP 400"), so every date of the
dataset journals FAILED and a backfill lists the market as failed."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar

import pytest

from ingest.context import IngestContext
from ingest.jobs import MARKET_VALUE_JOB, run_whole_market_job
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.ingest.test_whole_market_runner import TRADING_DAY, RecordingPublisher
from tests.sources.finmind._helpers import patch_client, settings
from tests.sources.finmind._responses import PLAN_DENIED_400


async def test_plan_denied_dataset_is_unreachable_and_journals_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    patch_client(monkeypatch, PLAN_DENIED_400, status_code=400)
    adapter = FinMindAdapter(settings=settings())

    result = await adapter.fetch_whole_market(
        Capability.MARKET_VALUE, tls_policy=TLSPolicy.STRICT, start_date="2026-10-20"
    )
    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "FinMind HTTP 400"

    ctx = IngestContext(
        finmind=adapter,  # type: ignore[arg-type]
        exchange=None,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=RecordingPublisher(),
    )
    run = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)
    assert run.status is RunStatus.FAILED
    assert run.detail == "FinMind HTTP 400"
