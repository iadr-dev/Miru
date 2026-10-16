"""`ingest.backfill_cli` operator-trigger tests."""

from __future__ import annotations

import dataclasses
from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import ingest.backfill_cli as backfill_cli_module
import ingest.derived_catchup as derived_catchup_module
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


def _stub_non_finmind_daily_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_backfill_cli` also calls `_run_non_finmind_forward_only_jobs_for_today` (six real
    network-hitting jobs — MOPS/TWSE/TPEX/yfinance) and `_backfill_etf_expense_ratio` (SITCA,
    also real network). Every test exercising `run_backfill_cli`'s generic per-spec loop stubs
    both out so neither contributes a `JobRun` or makes a network call, keeping this file's own
    assertions scoped to the behavior each test actually means to exercise."""

    async def _noop(_ctx: IngestContext) -> tuple[JobRun, ...]:
        return ()

    async def _noop_with_years(_ctx: IngestContext, *, years: int) -> tuple[JobRun, ...]:
        return ()

    monkeypatch.setattr(backfill_cli_module, "_run_non_finmind_forward_only_jobs_for_today", _noop)
    monkeypatch.setattr(backfill_cli_module, "_backfill_etf_expense_ratio", _noop_with_years)


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
    _stub_non_finmind_daily_jobs(monkeypatch)

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


async def test_non_finmind_forward_only_jobs_all_run_for_today_and_land_in_the_aggregate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the gap a live audit found: `etf_nav`/`etf_aum` (and the four MOPS/
    TWSE/TPEX forward-only jobs) are not `WholeMarketJobSpec`s, so the generic per-spec loop
    never reaches them — `run_backfill_cli` must call `_run_non_finmind_forward_only_jobs_for_
    today` itself and fold its six runs into the returned tuple, or a stale gap in exactly these
    six datasets is invisible to an operator reading the CLI's own summary."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    settings = _settings()
    ctx = _ctx(tmp_path)

    async def _fake_preflight(
        spec: WholeMarketJobSpec,
        _ctx: IngestContext,
        *,
        ingest_on_startup: bool,
        dev_backfill_years: int,
        now: object | None = None,
    ) -> tuple[JobRun, ...]:
        return ()

    async def _fake_shareholding(run_date: date, _ctx: IngestContext) -> JobRun:
        return JobRun(
            dataset=SHAREHOLDING_DISTRIBUTION_JOB.dataset,
            run_date=run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
        )

    async def _noop_expense_ratio_backfill(
        _ctx: IngestContext, *, years: int
    ) -> tuple[JobRun, ...]:
        return ()

    called: list[str] = []

    def _fake_job(name: str) -> object:
        async def _job(run_date: date, _ctx: IngestContext) -> JobRun:
            called.append(name)
            return JobRun(dataset=name, run_date=run_date, rows_written=1, status=RunStatus.SUCCESS)

        return _job

    monkeypatch.setattr(backfill_cli_module, "run_preflight_if_enabled", _fake_preflight)
    monkeypatch.setattr(backfill_cli_module, "job_shareholding_distribution", _fake_shareholding)
    monkeypatch.setattr(
        backfill_cli_module, "_backfill_etf_expense_ratio", _noop_expense_ratio_backfill
    )
    monkeypatch.setattr(backfill_cli_module, "job_etf_nav", _fake_job("etf_nav"))
    monkeypatch.setattr(backfill_cli_module, "job_etf_aum", _fake_job("etf_aum"))
    monkeypatch.setattr(
        backfill_cli_module, "job_material_announcements", _fake_job("material_announcements")
    )
    monkeypatch.setattr(
        backfill_cli_module,
        "job_insider_transfer_declaration",
        _fake_job("insider_transfer_declaration"),
    )
    monkeypatch.setattr(backfill_cli_module, "job_attention_stocks", _fake_job("attention_stocks"))
    monkeypatch.setattr(
        backfill_cli_module, "job_disposition_events", _fake_job("disposition_events")
    )

    runs = await run_backfill_cli(settings, ctx, override=True, years=1)

    assert called == [
        "etf_nav",
        "etf_aum",
        "material_announcements",
        "insider_transfer_declaration",
        "attention_stocks",
        "disposition_events",
    ]
    non_finmind_runs = {r.dataset for r in runs} & set(called)
    assert non_finmind_runs == set(called)


async def test_etf_expense_ratio_backfill_walks_months_and_skips_known_ones(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for `_backfill_etf_expense_ratio`: unlike the six forward-only jobs
    above, SITCA's own report accepts a past month, so this one gets a real monthly walk over
    `years` — and a month already covered by a known partition must be skipped, mirroring
    `_backfill_shareholding_distribution`'s own skip-if-known-date behavior at weekly grain."""
    ctx = _ctx(tmp_path)

    # 2024-02 already has a real partition — must be skipped. 2024-01 and 2024-03 do not.
    ctx.store.write_partition(
        "etf_expense_ratio",
        ctx.market.value,
        date(2024, 2, 15),
        [
            {
                "symbol": "0050",
                "market": "TW",
                "as_of": "2024-02-15",
                "management_fee_pct": 0.32,
                "custody_fee_pct": 0.035,
                "total_expense_ratio_pct_approx": 0.355,
                "custodian_bank": "台灣集中保管結算所股份有限公司",
            }
        ],
    )

    called: list[date] = []

    async def _fake_job(run_date: date, _ctx: IngestContext) -> JobRun:
        called.append(run_date)
        return JobRun(
            dataset="etf_expense_ratio", run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )

    monkeypatch.setattr(backfill_cli_module, "job_etf_expense_ratio", _fake_job)
    monkeypatch.setattr(
        backfill_cli_module,
        "last_available_trading_day",
        lambda _calendar, *, now: date(2024, 3, 29),
    )
    monkeypatch.setattr(
        TradingDayCalendar,
        "shift_business_days",
        lambda self, day, offset: date(2024, 1, 2),
    )

    runs = await backfill_cli_module._backfill_etf_expense_ratio(ctx, years=1)

    # January and March were fetched (resolved to their first real trading day); February,
    # already covered by the known partition above, made no call at all.
    assert called == [date(2024, 3, 1), date(2024, 1, 1)]
    assert len(runs) == 2
    assert all(r.status is RunStatus.SUCCESS for r in runs)


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

    monkeypatch.setattr(derived_catchup_module, "job_screener_flags", _fake_screener_flags)
    monkeypatch.setattr(derived_catchup_module, "job_scoring_batch", _fake_scoring_batch)
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


async def test_derived_compute_catchup_scores_a_day_whose_screener_already_landed(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-15: the 20:30 screener landed, the 23:55 scoring batch never ran. Scoring gaps are
    read from `stock_scores` itself, so that day is scored without re-running its screener, and
    days before the first scored day are left alone."""
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

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def _fake_scored_dates(_session: object, *, market: object) -> frozenset[date]:
        return frozenset({date(2024, 1, 2), date(2024, 1, 4)})

    window = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
    ctx = dataclasses.replace(_ctx(tmp_path), session_factory=_Session)  # type: ignore[arg-type]
    for d in window:
        ctx.store.write_partition("screener_flags", ctx.market.value, d, [])

    monkeypatch.setattr(derived_catchup_module, "job_screener_flags", _fake_screener_flags)
    monkeypatch.setattr(derived_catchup_module, "job_scoring_batch", _fake_scoring_batch)
    monkeypatch.setattr(derived_catchup_module, "scored_dates", _fake_scored_dates)
    monkeypatch.setattr(
        backfill_cli_module, "last_available_trading_day", lambda _calendar, *, now: window[-1]
    )
    monkeypatch.setattr(
        TradingDayCalendar, "shift_business_days", lambda self, day, offset: window[0]
    )
    monkeypatch.setattr(
        TradingDayCalendar, "business_days_between", lambda self, start, end: window
    )

    await run_derived_compute_catchup(_settings(), ctx, override=True, years=1)

    assert screener_calls == []
    # 01-03 is the gap; 01-01 predates the first scored day, so it is not back-filled.
    assert scoring_calls == [date(2024, 1, 3)]


async def test_us_scoring_catchup_scores_the_sessions_the_daemon_missed(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US scores stopped at 2026-10-12 because only the 07:30 daemon run scores US. The US
    backfill now scores each missing session after the first scored one, against a US-scoped
    context."""
    scored: list[tuple[date, str]] = []

    async def _fake_scoring_batch(run_date: date, run_ctx: IngestContext) -> JobRun:
        scored.append((run_date, run_ctx.market.value))
        return JobRun(
            dataset="stock_scores", run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def _fake_scored_dates(_session: object, *, market: object) -> frozenset[date]:
        assert str(market) == "US"
        return frozenset({date(2026, 10, 9), date(2026, 10, 12)})

    monkeypatch.setattr(derived_catchup_module, "job_scoring_batch", _fake_scoring_batch)
    monkeypatch.setattr(derived_catchup_module, "scored_dates", _fake_scored_dates)
    ctx = dataclasses.replace(_ctx(tmp_path), session_factory=_Session)  # type: ignore[arg-type]
    sessions = (
        date(2026, 10, 8),
        date(2026, 10, 9),
        date(2026, 10, 12),
        date(2026, 10, 13),
        date(2026, 10, 14),
    )

    await derived_catchup_module.catch_up_us(ctx, sessions, target=sessions[-1])

    assert scored == [(date(2026, 10, 13), "US"), (date(2026, 10, 14), "US")]


async def test_finmind_refusing_after_its_quota_reset_stops_the_whole_run(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike one spec failing, a FinMind that still answers 402 after its window reset (a lapsed
    plan or token) would refuse every later spec too, each after waiting out the hour. The run
    stops at the first one instead of spending about fourteen hours being refused."""
    from sources.finmind import FinMindQuotaRefusedError  # noqa: PLC0415 - local to this case

    attempted: list[str] = []

    async def _refused(spec: WholeMarketJobSpec, _ctx: IngestContext, **_kwargs: object) -> None:
        attempted.append(spec.dataset)
        raise FinMindQuotaRefusedError("FinMind refused again (402)")

    monkeypatch.setattr(backfill_cli_module, "run_preflight_if_enabled", _refused)
    _stub_non_finmind_daily_jobs(monkeypatch)

    with pytest.raises(FinMindQuotaRefusedError):
        await run_backfill_cli(_settings(), _ctx(tmp_path), override=True, years=1)
    assert attempted == [DAILY_WHOLE_MARKET_JOBS[0].dataset]
