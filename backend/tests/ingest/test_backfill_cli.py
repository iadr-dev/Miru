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
    run_derived_compute_catchup,
)
from ingest.context import IngestContext
from ingest.jobs import (
    DAILY_WHOLE_MARKET_JOBS,
    PRICE_EOD_JOB,
    SHAREHOLDING_DISTRIBUTION_JOB,
    WholeMarketJobSpec,
)
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


def test_daily_whole_market_jobs_is_the_spec_list_the_cli_walks() -> None:
    """The CLI's default `specs` argument must be exactly `jobs.DAILY_WHOLE_MARKET_JOBS` — proves
    a newly added daily job spec is picked up automatically rather than requiring a hand-copied
    list to be updated here too. `SHAREHOLDING_DISTRIBUTION_JOB` (weekly) is deliberately excluded
    from this generic per-calendar-day sweep — `run_backfill_cli` covers it separately via
    `_backfill_shareholding_distribution`."""
    kwdefaults = run_backfill_cli.__kwdefaults__
    assert kwdefaults is not None
    assert kwdefaults["specs"] is DAILY_WHOLE_MARKET_JOBS
    assert PRICE_EOD_JOB in DAILY_WHOLE_MARKET_JOBS
    assert SHAREHOLDING_DISTRIBUTION_JOB not in DAILY_WHOLE_MARKET_JOBS


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
    """`--yes`-equivalent `override=True` iterates every spec in `DAILY_WHOLE_MARKET_JOBS`; one
    spec raising must not prevent the remaining specs from running."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    settings = _settings()
    ctx = _ctx(tmp_path)

    failing_dataset = DAILY_WHOLE_MARKET_JOBS[0].dataset
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

    async def _fake_shareholding(run_date: date, _ctx: IngestContext) -> JobRun:
        # The weekly shareholding sweep now runs separately from the generic per-spec loop
        # above — stub it out here too so this test only asserts on the generic loop's own
        # per-spec attempt/failure behavior, not on real FinMind traffic.
        return JobRun(
            dataset=SHAREHOLDING_DISTRIBUTION_JOB.dataset,
            run_date=run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
        )

    monkeypatch.setattr(backfill_cli_module, "run_preflight_if_enabled", _fake_preflight)
    monkeypatch.setattr(backfill_cli_module, "job_shareholding_distribution", _fake_shareholding)

    runs = await run_backfill_cli(settings, ctx, override=True, years=1)

    # Every spec was attempted, including the ones after the failing one.
    assert attempted == [spec.dataset for spec in DAILY_WHOLE_MARKET_JOBS]
    # The failing spec contributed no JobRun; every other spec's single fake run is present,
    # plus the stubbed weekly shareholding sweep's own runs.
    generic_runs = [r for r in runs if r.dataset != SHAREHOLDING_DISTRIBUTION_JOB.dataset]
    assert len(generic_runs) == len(DAILY_WHOLE_MARKET_JOBS) - 1
    assert all(r.status is RunStatus.SUCCESS for r in generic_runs)
    assert failing_dataset not in {r.dataset for r in generic_runs}


async def test_shareholding_backfill_skips_weeks_already_covered_by_a_known_partition(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: `_backfill_shareholding_distribution` must not re-fetch a week whose
    resolved publish-date partition already exists. The check must compare against the RESOLVED
    date (which can differ from the weekly cursor by up to `_WEEKLY_PUBLISH_SEARCH_DAYS` days),
    not `has_partition(cursor)` directly — a naive cursor-only check would always miss and
    re-fetch every already-covered week, defeating the whole point of this skip."""
    ctx = _ctx(tmp_path)

    # A real (non-empty) partition exists at the RESOLVED publish date (a Friday), one day
    # earlier than where the weekly cursor will land (a Saturday) — exercising the
    # resolved-vs-cursor gap. `list_partition_dates` (what the skip check reads) excludes
    # empty-row placeholder partitions, so this needs at least one real row.
    ctx.store.write_partition(
        SHAREHOLDING_DISTRIBUTION_JOB.dataset,
        ctx.market.value,
        date(2024, 1, 5),
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2024-01-05",
                "holding_shares_level": "1-999",
                "people": 100,
                "percent": 1.0,
                "unit": 1,
            }
        ],
    )

    called: list[date] = []

    async def _fake_shareholding(run_date: date, _ctx: IngestContext) -> JobRun:
        called.append(run_date)
        return JobRun(
            dataset=SHAREHOLDING_DISTRIBUTION_JOB.dataset,
            run_date=run_date,
            rows_written=1,
            status=RunStatus.SUCCESS,
        )

    monkeypatch.setattr(backfill_cli_module, "job_shareholding_distribution", _fake_shareholding)
    monkeypatch.setattr(
        backfill_cli_module,
        "last_available_trading_day",
        lambda _calendar, *, now: date(2024, 1, 6),  # a Saturday, 1 day after the resolved Friday
    )
    monkeypatch.setattr(
        TradingDayCalendar, "shift_business_days", lambda self, day, offset: date(2024, 1, 6)
    )

    runs = await backfill_cli_module._backfill_shareholding_distribution(ctx, years=0)

    # The one week in range was already covered by the 2024-01-05 partition — no FinMind call.
    assert called == []
    assert runs == ()


async def test_derived_compute_catchup_covers_every_missing_day_not_just_the_latest(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a dev box down across a multi-day window (e.g. a long weekend plus a
    rebuild) must get `screener_flags`/`stock_scores` computed for EVERY missing trading day in
    the gap, not only the single most recent one — otherwise an in-between day is permanently
    skipped even after the underlying whole-market data for it has been backfilled."""
    settings = _settings()
    ctx = _ctx(tmp_path)

    screener_calls: list[date] = []
    scoring_calls: list[date] = []

    async def _fake_screener_flags(run_date: date, _ctx: IngestContext) -> JobRun:
        screener_calls.append(run_date)
        return JobRun(
            dataset="screener_flags", run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )

    async def _fake_scoring_batch(run_date: date, _ctx: IngestContext) -> JobRun:
        scoring_calls.append(run_date)
        return JobRun(
            dataset="stock_scores", run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )

    # Every business day in the window already has a partition EXCEPT the two-day gap
    # (2024-01-01, 2024-01-02) this catch-up must still cover — a narrower window than the real
    # `years * 250 trading days` default, via a monkeypatched calendar walker, so this test
    # doesn't have to hand-write a full year of business days.
    target = date(2024, 1, 3)
    gap = {date(2024, 1, 1), date(2024, 1, 2)}
    window = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))
    for d in window:
        if d not in gap:
            ctx.store.write_partition("screener_flags", ctx.market.value, d, [])

    monkeypatch.setattr(backfill_cli_module, "job_screener_flags", _fake_screener_flags)
    monkeypatch.setattr(backfill_cli_module, "job_scoring_batch", _fake_scoring_batch)
    monkeypatch.setattr(
        backfill_cli_module, "last_available_trading_day", lambda _calendar, *, now: target
    )
    monkeypatch.setattr(
        TradingDayCalendar, "shift_business_days", lambda self, day, offset: window[0]
    )
    monkeypatch.setattr(
        TradingDayCalendar, "business_days_between", lambda self, start, end: window
    )

    await run_derived_compute_catchup(settings, ctx, override=True, years=1)

    # The two missing days were both computed, in order; the already-present target day was not.
    assert screener_calls == [date(2024, 1, 1), date(2024, 1, 2)]
    assert scoring_calls == [date(2024, 1, 1), date(2024, 1, 2)]
