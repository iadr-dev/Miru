"""`job_us_fundamentals` keeps a few symbols in memory, checkpoints to disk, and a run killed
part-way continues from the last checkpoint instead of repeating hours of paced calls."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import ingest.us_jobs as us_jobs_module
import sources.yfinance._adapter as yfinance_adapter_module
from ingest.journal import RunStatus
from ingest.spool import SPOOL_DIRNAME
from ingest.us_jobs import job_us_fundamentals
from schemas.enums import Capability
from schemas.market import Symbol
from schemas.source import SourceResult
from tests.ingest.test_us_jobs import _ctx, _seed_universe, _stub_result

RUN_DATE = date(2024, 1, 2)
SYMBOLS = ["S1", "S2", "S3", "S4", "S5"]
CAPABILITIES = 9


class _Killed(BaseException):
    """Not an `Exception`: nothing in the job may swallow it, like a SIGKILL."""


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kill_at: str | None,
    calls: list[str],
    fail: str | None = None,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    async def fake_fetch(
        self: object, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        if sym.id == kill_at:
            raise _Killed
        if sym.id == fail:
            raise RuntimeError("upstream boom")
        calls.append(sym.id)
        return _stub_result({"lines": [{"symbol": sym.id, "value": 1}]})

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)
    monkeypatch.setattr(us_jobs_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(us_jobs_module, "_CHECKPOINT_EVERY_SYMBOLS", 2)


async def test_a_killed_run_continues_from_its_last_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _seed_universe(ctx, RUN_DATE, SYMBOLS)
    _install(monkeypatch, kill_at="S4", calls=[])

    with pytest.raises(_Killed):
        await job_us_fundamentals(RUN_DATE, ctx)  # dies on S4; S1-S2 were checkpointed

    second_calls: list[str] = []
    _install(monkeypatch, kill_at=None, calls=second_calls)
    run = await job_us_fundamentals(RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert set(second_calls) == {"S3", "S4", "S5"}  # S1, S2 were not fetched again
    # Every row exactly once: the stub gives each capability one row per symbol.
    assert run.rows_written == len(SYMBOLS) * CAPABILITIES


async def test_the_spool_is_removed_after_a_successful_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _seed_universe(ctx, RUN_DATE, SYMBOLS)
    _install(monkeypatch, kill_at=None, calls=[])

    await job_us_fundamentals(RUN_DATE, ctx)

    spool_root = tmp_path / SPOOL_DIRNAME
    assert not spool_root.exists() or not any(spool_root.iterdir())


async def test_failures_from_before_the_kill_still_make_the_run_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _seed_universe(ctx, RUN_DATE, SYMBOLS)
    _install(monkeypatch, kill_at="S4", calls=[], fail="S1")
    with pytest.raises(_Killed):
        await job_us_fundamentals(RUN_DATE, ctx)

    _install(monkeypatch, kill_at=None, calls=[])
    run = await job_us_fundamentals(RUN_DATE, ctx)

    assert run.status is RunStatus.PARTIAL  # S1's failures were remembered across the kill


async def test_a_spool_from_another_date_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    other_date = date(2024, 1, 3)
    _seed_universe(ctx, RUN_DATE, SYMBOLS)
    _seed_universe(ctx, other_date, SYMBOLS)
    _install(monkeypatch, kill_at="S4", calls=[])
    with pytest.raises(_Killed):
        await job_us_fundamentals(RUN_DATE, ctx)

    calls: list[str] = []
    _install(monkeypatch, kill_at=None, calls=calls)
    await job_us_fundamentals(other_date, ctx)

    assert set(calls) == set(SYMBOLS)
