"""`ingest.backfill_cli` operator-trigger tests."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import ingest.backfill_cli as backfill_cli_module
import sources.finmind as finmind_module
from config.settings import Settings
from ingest.backfill_cli import (
    ProductionHardStopError,
    _check_not_production,
    run_backfill_cli,
)
from ingest.context import IngestContext
from ingest.jobs import ALL_WHOLE_MARKET_JOBS, PRICE_EOD_JOB, WholeMarketJobSpec
from ingest.journal import JobRun, RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _settings(**overrides: object) -> Settings:
    # `_env_file=None` so a developer's local `.env` (which may set `INGEST_ON_STARTUP=true`
    # for their own dev workflow) can never leak into these tests' expectations.
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[arg-type]
        finmind_api_key="test-token",
        **overrides,  # type: ignore[arg-type]
    )


def _ctx(tmp_path: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=_settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def test_all_whole_market_jobs_is_the_spec_list_the_cli_walks() -> None:
    """The CLI's default `specs` argument must be exactly `jobs.ALL_WHOLE_MARKET_JOBS` — proves
    a newly added job spec is picked up automatically rather than requiring a hand-copied list
    to be updated here too."""
    kwdefaults = run_backfill_cli.__kwdefaults__
    assert kwdefaults is not None
    assert kwdefaults["specs"] is ALL_WHOLE_MARKET_JOBS
    assert PRICE_EOD_JOB in ALL_WHOLE_MARKET_JOBS


def test_production_hard_stop_is_unconditional() -> None:
    prod_settings = _settings(env="production")
    with pytest.raises(ProductionHardStopError):
        _check_not_production(prod_settings)


async def test_refuses_without_override_or_flag(tmp_path: object) -> None:
    """`ingest_on_startup=False` and no `--yes` override -> refuses, runs nothing."""
    settings = _settings()  # ingest_on_startup defaults to False
    ctx = _ctx(tmp_path)

    runs = await run_backfill_cli(settings, ctx, override=False, years=1)

    assert runs == ()


async def test_production_hard_stop_wins_even_with_override(tmp_path: object) -> None:
    """The production hard-stop fires even when `override=True` is passed — no override can
    paper over `ENV=production`."""
    settings = _settings(env="production")
    ctx = _ctx(tmp_path)

    with pytest.raises(ProductionHardStopError):
        await run_backfill_cli(settings, ctx, override=True, years=1)


async def test_override_runs_every_spec_and_one_failure_does_not_block_the_rest(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--yes`-equivalent `override=True` iterates every spec in `ALL_WHOLE_MARKET_JOBS`; one
    spec raising must not prevent the remaining specs from running."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    settings = _settings()
    ctx = _ctx(tmp_path)

    failing_dataset = ALL_WHOLE_MARKET_JOBS[0].dataset
    attempted: list[str] = []

    async def _fake_preflight(
        spec: WholeMarketJobSpec,
        _ctx: IngestContext,
        *,
        ingest_on_startup: bool,
        dev_backfill_years: int,
        now: object | None = None,
    ) -> tuple[JobRun, ...]:
        attempted.append(spec.dataset)
        if spec.dataset == failing_dataset:
            raise RuntimeError("boom — simulated spec failure")
        return (
            JobRun(
                dataset=spec.dataset,
                run_date=date(2024, 1, 2),
                rows_written=1,
                status=RunStatus.SUCCESS,
            ),
        )

    monkeypatch.setattr(backfill_cli_module, "run_preflight_if_enabled", _fake_preflight)

    runs = await run_backfill_cli(settings, ctx, override=True, years=1)

    # Every spec was attempted, including the ones after the failing one.
    assert attempted == [spec.dataset for spec in ALL_WHOLE_MARKET_JOBS]
    # The failing spec contributed no JobRun; every other spec's single fake run is present.
    assert len(runs) == len(ALL_WHOLE_MARKET_JOBS) - 1
    assert all(r.status is RunStatus.SUCCESS for r in runs)
    assert failing_dataset not in {r.dataset for r in runs}
