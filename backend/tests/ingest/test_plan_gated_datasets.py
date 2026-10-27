"""A dataset outside the FinMind plan is a tier-skip, not a failure: journaled SKIPPED, remembered
for a bounded time, not retried date after date, and counted apart in the backfill summary. A bad
key, the rate limit, a 5xx or a dead network still fail the run."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

import ingest.backfill_cli as cli
import sources.finmind as finmind_module
from config.settings import Settings
from ingest.backfill import backfill_range
from ingest.context import IngestContext
from ingest.jobs import DIVIDENDS_JOB, MARKET_VALUE_JOB, PRICE_EOD_JOB, run_whole_market_job
from ingest.jobs.source_chain import EXCHANGE_ID, PLAN_DENIAL_TTL
from ingest.journal import JobRun, RunStatus
from ingest.store import ParquetStore
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.ingest.test_whole_market_runner import TRADING_DAY, StubSource, _bar, _ctx, _result
from tests.sources.finmind._helpers import patch_client, settings
from tests.sources.finmind._responses import (
    BAD_TOKEN_400,
    IP_BANNED_403,
    PLAN_DENIED_400,
    RATE_LIMIT_402,
)

DENIED = _result(
    [],
    status=HealthStatus.UNAVAILABLE_TIER,
    detail="not in the FinMind plan: TaiwanStockMarketValue",
)
JAN = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
MARKET = "TW"
CAP = Capability.MARKET_VALUE.value


async def test_a_plan_denied_dataset_journals_skipped_and_flagged(tmp_path: object) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)

    run = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.tier_skipped is True
    assert run.rows_written == 0
    assert run.detail == "not in the FinMind plan: TaiwanStockMarketValue; no free source"
    assert not ctx.store.has_partition(MARKET_VALUE_JOB.dataset, MARKET, TRADING_DAY)
    assert not ctx.store.is_closed(MARKET_VALUE_JOB.dataset, MARKET, TRADING_DAY)  # not a closure


async def test_the_denial_is_remembered_so_later_dates_are_not_sent_to_finmind(
    tmp_path: object,
) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)

    runs = [await run_whole_market_job(MARKET_VALUE_JOB, d, ctx) for d in JAN]

    assert len(finmind.calls) == 1
    assert all(r.tier_skipped and r.status is RunStatus.SKIPPED for r in runs)
    assert all(r.detail == runs[0].detail for r in runs)  # journaled consistently


async def test_a_backfill_probes_a_plan_gated_dataset_once_and_stops(tmp_path: object) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)

    runs = await backfill_range(
        MARKET_VALUE_JOB, ctx, start=date(2024, 1, 2), end=date(2024, 3, 29), only_missing=False
    )

    assert len(finmind.calls) == 1
    assert len(runs) == 1
    assert runs[0].tier_skipped


async def test_only_missing_reports_a_remembered_denial_without_asking_finmind(
    tmp_path: object,
) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)
    ctx.store.mark_plan_denied(MARKET, CAP)

    runs = await backfill_range(
        MARKET_VALUE_JOB, ctx, start=date(2024, 1, 2), end=date(2024, 3, 29), only_missing=True
    )

    assert finmind.calls == []
    assert [r.tier_skipped for r in runs] == [True]


async def test_the_denial_expires_so_an_upgraded_plan_fills_the_dataset(tmp_path: object) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)
    stale = datetime.now(UTC) - PLAN_DENIAL_TTL - timedelta(minutes=1)
    ctx.store.mark_plan_denied(MARKET, CAP, now=stale)
    assert not ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)
    finmind.result = _result([_bar("2330")], detail="ok")  # the plan was upgraded

    run = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)

    assert len(finmind.calls) == 1
    assert run.status is RunStatus.SUCCESS


async def test_forgetting_the_denials_asks_finmind_again_at_once(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, StubSource(DENIED))
    ctx.store.mark_plan_denied(MARKET, CAP)
    assert ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)

    assert ctx.store.clear_plan_denials() == 1

    assert not ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)
    assert ctx.store.clear_plan_denials() == 0


async def test_the_marker_is_outside_every_partition_scan(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, StubSource(DENIED))
    ctx.store.mark_plan_denied(MARKET, CAP)

    assert ctx.store.list_partition_dates(MARKET_VALUE_JOB.dataset, MARKET) == ()
    assert ctx.store.freshness_token(MARKET) == ()


async def test_a_denial_is_per_capability(tmp_path: object) -> None:
    finmind = StubSource(DENIED)
    ctx = _ctx(tmp_path, finmind)
    await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)

    other = StubSource(DENIED)
    run = await run_whole_market_job(DIVIDENDS_JOB, TRADING_DAY, _ctx(tmp_path, other))

    assert len(other.calls) == 1  # asked once, not skipped on MARKET_VALUE's account
    assert run.tier_skipped


async def test_a_failing_official_source_keeps_the_day_failed_despite_the_plan_denial(
    tmp_path: object,
) -> None:
    """Price is `[exchange_official, finmind]`: FinMind refusing for the plan does not hide the
    exchange failing - that is a genuine failure of the day."""
    official = StubSource(
        _result([], status=HealthStatus.UNREACHABLE, detail="TWSE: HTTP 503", source_id=EXCHANGE_ID)
    )
    ctx = _ctx(tmp_path, StubSource(DENIED))
    ctx = IngestContext(
        finmind=ctx.finmind,
        exchange=official,
        store=ctx.store,
        leader=ctx.leader,
        calendar=ctx.calendar,
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=ctx.data_update_publisher,
    )

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert not run.tier_skipped


def _transport_error_client(**_kw: object) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param(BAD_TOKEN_400, 400, id="expired-or-invalid-key"),
        pytest.param(RATE_LIMIT_402, 402, id="rate-limit"),
        pytest.param(IP_BANNED_403, 403, id="ip-banned"),
        pytest.param({"msg": "internal error", "status": 500}, 500, id="outage-5xx"),
        pytest.param(None, 0, id="network-error"),
    ],
)
async def test_real_failures_still_fail_the_run_and_leave_no_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, body: Any, status: int
) -> None:
    if body is None:
        monkeypatch.setattr(finmind_module, "build_async_client", _transport_error_client)
    else:
        patch_client(monkeypatch, body, status_code=status)
    ctx = IngestContext(
        finmind=FinMindAdapter(settings=settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=_ctx(tmp_path, StubSource(DENIED)).leader,
        calendar=_ctx(tmp_path, StubSource(DENIED)).calendar,
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=_ctx(tmp_path, StubSource(DENIED)).data_update_publisher,
    )

    run = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert not run.tier_skipped
    assert not ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)


async def test_the_real_adapter_plan_denial_journals_a_tier_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    patch_client(monkeypatch, PLAN_DENIED_400, status_code=400)
    base = _ctx(tmp_path, StubSource(DENIED))
    ctx = IngestContext(
        finmind=FinMindAdapter(settings=settings()),
        store=base.store,
        leader=base.leader,
        calendar=base.calendar,
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=base.data_update_publisher,
    )

    run = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.tier_skipped
    assert ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)


def _run(dataset: str, status: RunStatus, *, tier: bool = False) -> JobRun:
    return JobRun(dataset, TRADING_DAY, 0, status, tier_skipped=tier)


async def _summarize(
    monkeypatch: pytest.MonkeyPatch, runs: tuple[JobRun, ...], *, recheck: bool = False
) -> int:
    async def fake_backfill(*_a: object, **_k: object) -> tuple[JobRun, ...]:
        return runs

    async def none(*_a: object, **_k: object) -> tuple[JobRun, ...]:
        return ()

    monkeypatch.setattr(cli, "run_backfill_cli", fake_backfill)
    monkeypatch.setattr(cli, "run_derived_compute_catchup", none)
    monkeypatch.setattr(cli, "_optional_sc_research", none)
    args = argparse.Namespace(
        fill_missing_companies=None,
        market="tw",
        yes=True,
        recheck_plan=recheck,
        event_history=False,
    )
    ctx = _ctx("unused", StubSource(DENIED))
    return await cli._run_backfill(args, Settings(), ctx, 1)


async def test_summary_lists_tier_skipped_datasets_and_exits_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = (
        _run("TaiwanStockMarketValue", RunStatus.SKIPPED, tier=True),
        _run("TaiwanStockDividend", RunStatus.SKIPPED, tier=True),
        _run("TaiwanStockPrice", RunStatus.SUCCESS),
    )

    code = await _summarize(monkeypatch, runs)

    assert code == 0
    out = capsys.readouterr().out
    assert "tier-skipped" in out
    assert "TaiwanStockDividend, TaiwanStockMarketValue" in out


async def test_summary_still_exits_nonzero_for_a_real_failure_beside_tier_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = (
        _run("TaiwanStockMarketValue", RunStatus.SKIPPED, tier=True),
        _run("TaiwanStockPrice", RunStatus.FAILED),
    )

    assert await _summarize(monkeypatch, runs) == 1


async def test_recheck_plan_forgets_the_remembered_denials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    async def none(*_a: object, **_k: object) -> tuple[JobRun, ...]:
        return ()

    monkeypatch.setattr(cli, "run_backfill_cli", none)
    monkeypatch.setattr(cli, "run_derived_compute_catchup", none)
    monkeypatch.setattr(cli, "_optional_sc_research", none)
    ctx = _ctx(tmp_path, StubSource(DENIED))
    ctx.store.mark_plan_denied(MARKET, CAP)
    args = cli.parse_args(["--yes", "--recheck-plan"])

    await cli._run_backfill(args, Settings(), ctx, 1)

    assert not ctx.store.is_plan_denied(MARKET, CAP, ttl=PLAN_DENIAL_TTL)
