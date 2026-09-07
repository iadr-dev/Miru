"""`ingest.__main__`'s startup gap-fill — gating and TW/US concurrency."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

import ingest.__main__ as main_module
from config.settings import Settings
from ingest.__main__ import _startup_gap_fill
from ingest.journal import JobRun, RunStatus


def _settings(**overrides: object) -> Settings:
    # `_env_file=None` so a developer's local `.env` can never leak into these tests.
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[arg-type]
        finmind_api_key="test-token",
        **overrides,  # type: ignore[arg-type]
    )


def _job_run(status: RunStatus = RunStatus.SUCCESS) -> JobRun:
    return JobRun(
        dataset="us_fundamentals", run_date=date(2024, 1, 1), rows_written=0, status=status
    )


async def test_disabled_flag_never_calls_any_gap_fill_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ingest_on_startup=False` (the production default) must skip every gap-fill path
    entirely — no TW call, no US call, no derived-compute call, no fundamentals call, no MOPS
    calls."""
    settings = _settings()  # ingest_on_startup defaults to False
    called: list[str] = []

    async def _fail_if_called(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        called.append("called")
        return ()

    async def _fail_if_called_job(*_args: object, **_kwargs: object) -> JobRun:
        called.append("called")
        return _job_run()

    monkeypatch.setattr(main_module, "run_backfill_cli", _fail_if_called)
    monkeypatch.setattr(main_module, "run_derived_compute_catchup", _fail_if_called)
    monkeypatch.setattr(main_module, "run_us_backfill_cli", _fail_if_called)
    monkeypatch.setattr(main_module, "job_us_fundamentals", _fail_if_called_job)
    monkeypatch.setattr(main_module, "job_material_announcements", _fail_if_called_job)
    monkeypatch.setattr(main_module, "job_insider_transfer_declaration", _fail_if_called_job)
    monkeypatch.setattr(main_module, "job_attention_stocks", _fail_if_called_job)
    monkeypatch.setattr(main_module, "job_disposition_events", _fail_if_called_job)

    await _startup_gap_fill(settings, ctx=object())  # type: ignore[arg-type]

    assert called == []


async def test_tw_chain_and_us_chain_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the TW chain (whole-market pulls + derived-compute) and the US chain
    (price backfill + fundamentals catch-up) must run CONCURRENTLY, not sequentially — a slow
    multi-year TW derived-compute catchup must not delay the US chain from even starting. Proven
    by having each side wait on the other's start signal before proceeding: if they were
    sequential, this would deadlock/timeout instead of completing."""
    settings = _settings(ingest_on_startup=True)
    tw_started = asyncio.Event()
    us_started = asyncio.Event()

    async def _fake_backfill_cli(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        tw_started.set()
        await asyncio.wait_for(us_started.wait(), timeout=1.0)
        return ()

    async def _fake_derived_catchup(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    async def _fake_us_backfill(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        us_started.set()
        await asyncio.wait_for(tw_started.wait(), timeout=1.0)
        return ()

    async def _fake_fundamentals(*_args: object, **_kwargs: object) -> JobRun:
        return _job_run()

    monkeypatch.setattr(main_module, "run_backfill_cli", _fake_backfill_cli)
    monkeypatch.setattr(main_module, "run_derived_compute_catchup", _fake_derived_catchup)
    monkeypatch.setattr(main_module, "run_us_backfill_cli", _fake_us_backfill)
    monkeypatch.setattr(main_module, "job_us_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_material_announcements", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_insider_transfer_declaration", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_attention_stocks", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_disposition_events", _fake_fundamentals)

    # No timeout/deadlock — each side's wait resolves only because the other genuinely started
    # concurrently, not after it, which a sequential (non-`gather`) implementation could not do.
    await asyncio.wait_for(
        _startup_gap_fill(settings, ctx=object()),  # type: ignore[arg-type]
        timeout=2.0,
    )

    assert tw_started.is_set()
    assert us_started.is_set()


async def test_us_chain_runs_fundamentals_catchup_after_price_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`job_us_fundamentals` must be called as part of the startup gap-fill — otherwise it is
    only reachable via its own WEEKLY cron slot, and a dev restart landing between two weekly
    fire windows leaves it stuck until the next one (the same class of gap `_tw_gap_fill`'s
    derived-compute catchup exists to close for TW)."""
    settings = _settings(ingest_on_startup=True)
    fundamentals_calls: list[date] = []

    async def _ok(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    async def _fake_fundamentals(run_date: date, _ctx: object) -> JobRun:
        fundamentals_calls.append(run_date)
        return _job_run()

    async def _ok_job(*_args: object, **_kwargs: object) -> JobRun:
        return _job_run()

    monkeypatch.setattr(main_module, "run_backfill_cli", _ok)
    monkeypatch.setattr(main_module, "run_derived_compute_catchup", _ok)
    monkeypatch.setattr(main_module, "run_us_backfill_cli", _ok)
    monkeypatch.setattr(main_module, "job_us_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_material_announcements", _ok_job)
    monkeypatch.setattr(main_module, "job_insider_transfer_declaration", _ok_job)
    monkeypatch.setattr(main_module, "job_attention_stocks", _ok_job)
    monkeypatch.setattr(main_module, "job_disposition_events", _ok_job)

    await _startup_gap_fill(settings, ctx=object())  # type: ignore[arg-type]

    assert len(fundamentals_calls) == 1


async def test_tw_chain_runs_mops_and_disposition_board_jobs_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`job_material_announcements`/`job_insider_transfer_declaration`/`job_attention_stocks`/
    `job_disposition_events` must all be called as part of the TW gap-fill chain — none is a
    `WholeMarketJobSpec`, so `run_backfill_cli`'s own per-spec gap-fill never reaches them (same
    class of gap `job_us_fundamentals`'s own direct call closes for the US chain). This matters
    more for these four than for US fundamentals: none has a backfill path at all, so a day
    missed at the daemon's own cron fire time is otherwise permanently lost."""
    settings = _settings(ingest_on_startup=True)
    announcement_calls: list[date] = []
    insider_transfer_calls: list[date] = []
    attention_stocks_calls: list[date] = []
    disposition_events_calls: list[date] = []

    async def _ok(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    async def _fake_fundamentals(*_args: object, **_kwargs: object) -> JobRun:
        return _job_run()

    async def _fake_announcements(run_date: date, _ctx: object) -> JobRun:
        announcement_calls.append(run_date)
        return _job_run()

    async def _fake_insider_transfer(run_date: date, _ctx: object) -> JobRun:
        insider_transfer_calls.append(run_date)
        return _job_run()

    async def _fake_attention_stocks(run_date: date, _ctx: object) -> JobRun:
        attention_stocks_calls.append(run_date)
        return _job_run()

    async def _fake_disposition_events(run_date: date, _ctx: object) -> JobRun:
        disposition_events_calls.append(run_date)
        return _job_run()

    monkeypatch.setattr(main_module, "run_backfill_cli", _ok)
    monkeypatch.setattr(main_module, "run_derived_compute_catchup", _ok)
    monkeypatch.setattr(main_module, "run_us_backfill_cli", _ok)
    monkeypatch.setattr(main_module, "job_us_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(main_module, "job_material_announcements", _fake_announcements)
    monkeypatch.setattr(main_module, "job_insider_transfer_declaration", _fake_insider_transfer)
    monkeypatch.setattr(main_module, "job_attention_stocks", _fake_attention_stocks)
    monkeypatch.setattr(main_module, "job_disposition_events", _fake_disposition_events)

    await _startup_gap_fill(settings, ctx=object())  # type: ignore[arg-type]

    assert len(announcement_calls) == 1
    assert len(insider_transfer_calls) == 1
    assert len(attention_stocks_calls) == 1
    assert len(disposition_events_calls) == 1


async def test_startup_gap_fill_failure_is_never_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient upstream failure in either path must not raise out of `_startup_gap_fill` —
    the poll loop must still start regardless."""
    settings = _settings(ingest_on_startup=True)

    async def _boom(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        raise RuntimeError("simulated transient upstream failure")

    async def _ok(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    async def _ok_job(*_args: object, **_kwargs: object) -> JobRun:
        return _job_run()

    monkeypatch.setattr(main_module, "run_backfill_cli", _boom)
    monkeypatch.setattr(main_module, "run_derived_compute_catchup", _ok)
    monkeypatch.setattr(main_module, "run_us_backfill_cli", _ok)
    monkeypatch.setattr(main_module, "job_us_fundamentals", _ok_job)
    monkeypatch.setattr(main_module, "job_material_announcements", _ok_job)
    monkeypatch.setattr(main_module, "job_insider_transfer_declaration", _ok_job)
    monkeypatch.setattr(main_module, "job_attention_stocks", _ok_job)
    monkeypatch.setattr(main_module, "job_disposition_events", _ok_job)

    # Must not raise.
    await _startup_gap_fill(settings, ctx=object())  # type: ignore[arg-type]
