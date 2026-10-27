"""The source chain for the period-keyed datasets (monthly revenue): the official filings first,
FinMind only when they cannot serve, and a date that holds no rows by definition answered once,
without asking anyone else."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

from ingest.backfill import detect_missing_trading_days
from ingest.context import IngestContext
from ingest.jobs import (
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    FINANCIALS_JOB,
    MONTH_REVENUE_JOB,
    run_whole_market_job,
)
from ingest.jobs.source_chain import (
    FILINGS_ID,
    FINMIND_ID,
    load_whole_market_priority,
    resolve_chain,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.base import is_period_not_due
from sources.company_filings.adapter import CompanyFilingsAdapter
from tests.ingest.test_whole_market_runner import RecordingPublisher, StubSource

FIRST = date(2024, 1, 1)  # a Monday, the 1st: carries December 2023
SECOND = date(2024, 1, 2)
DETAIL = "MOPS t21sc03 2023-12: listed 1 + OTC 1 + emerging 0 rows"


def _row(symbol: str, trade_date: date = FIRST) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": trade_date.isoformat(),
        "country": "Taiwan",
        "revenue": 1_000_000,
        "revenue_month": 12,
        "revenue_year": 2023,
        "create_time": None,
    }


def _result(
    rows: list[dict[str, object]],
    status: HealthStatus = HealthStatus.OK,
    detail: str = "ok",
    source_id: str = "FINMIND",
) -> SourceResult:
    return SourceResult(
        source_id=source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub",
        payload={"rows": rows},
        health=SourceHealth(status=status, detail=detail),
    )


def _filings(
    rows: list[dict[str, object]] | None = None,
    status: HealthStatus = HealthStatus.OK,
    detail: str = DETAIL,
) -> StubSource:
    return StubSource(
        _result([_row("1101")] if rows is None else rows, status, detail, "OFFICIAL_FILINGS")
    )


def _finmind(
    rows: list[dict[str, object]] | None = None,
    status: HealthStatus = HealthStatus.OK,
    detail: str = "ok",
) -> StubSource:
    return StubSource(_result([_row("2330")] if rows is None else rows, status, detail))


def _ctx(
    tmp_path: object,
    finmind: StubSource,
    filings: StubSource | None,
    publisher: RecordingPublisher | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        filings=filings,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher or RecordingPublisher(),
    )


def _symbols(ctx: IngestContext, run_date: date = FIRST) -> list[str]:
    rows = ctx.store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", run_date)
    return sorted(str(r["symbol"]) for r in rows)


def test_the_configured_chain_asks_the_filings_before_finmind() -> None:
    priority = load_whole_market_priority()["TW"]
    for capability in (
        Capability.MONTH_REVENUE,
        Capability.FINANCIALS,
        Capability.BALANCE_SHEET,
        Capability.CASH_FLOW_STATEMENT,
    ):
        assert priority[capability.value] == [FILINGS_ID, FINMIND_ID]


def test_a_context_without_filings_is_finmind_alone() -> None:
    ctx = IngestContext(
        finmind=_finmind(),  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
    )

    chain = resolve_chain(ctx, Capability.MONTH_REVENUE)

    assert [link.source_id for link in chain] == [FINMIND_ID]


async def test_the_filings_serve_the_first_of_the_month_and_finmind_is_not_asked(
    tmp_path: object,
) -> None:
    finmind, filings = _finmind(), _filings()
    ctx = _ctx(tmp_path, finmind, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == f"served by {FILINGS_ID}: {DETAIL}"
    assert _symbols(ctx) == ["1101"]
    assert filings.calls[0][0] is Capability.MONTH_REVENUE
    assert filings.calls[0][1]["start_date"] == "2024-01-01"
    assert finmind.calls == []


async def test_a_date_with_no_period_is_a_journaled_success_and_nobody_else_is_asked(
    tmp_path: object,
) -> None:
    finmind = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
    filings = _filings([], HealthStatus.OK, "2024-01-02 is not the 1st of a month")
    ctx = _ctx(tmp_path, finmind, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, SECOND, ctx)

    assert run.status is RunStatus.SUCCESS  # not the ~4,500 failed runs of a 403 per empty day
    assert run.rows_written == 0
    assert run.detail == f"served by {FILINGS_ID}: 2024-01-02 is not the 1st of a month"
    assert finmind.calls == []
    assert ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", SECOND)
    assert not ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", SECOND)


async def test_not_published_yet_hands_over_to_finmind(tmp_path: object) -> None:
    filings = _filings([], HealthStatus.STALE, "revenue for 2023-12 is due by the 10th")
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == f"served by {FINMIND_ID}: ok (after {FILINGS_ID}: no rows)"
    assert _symbols(ctx) == ["2330"]
    assert len(finmind.calls) == 1


async def test_not_published_yet_and_finmind_refusing_is_a_failed_run_to_retry(
    tmp_path: object,
) -> None:
    filings = _filings([], HealthStatus.STALE, "revenue for 2023-12 is due by the 10th")
    finmind = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.status is RunStatus.FAILED
    assert run.detail == f"no source served: {FINMIND_ID}: FinMind HTTP 403; {FILINGS_ID}: no rows"
    assert not ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", FIRST)


async def test_a_failing_filings_source_falls_back_and_says_why(tmp_path: object) -> None:
    filings = _filings([], HealthStatus.UNREACHABLE, "otc: mopsov.twse.com.tw: HTTP 403")
    ctx = _ctx(tmp_path, _finmind(), filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == (
        f"served by {FINMIND_ID}: ok (after {FILINGS_ID}: otc: mopsov.twse.com.tw: HTTP 403)"
    )


async def test_a_malformed_filing_row_is_dropped_before_the_write(tmp_path: object) -> None:
    bad = {**_row("9999"), "revenue": None}
    ctx = _ctx(tmp_path, _finmind(), _filings([_row("1101"), bad]))

    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.rows_written == 1
    assert _symbols(ctx) == ["1101"]


async def test_an_empty_answer_for_a_date_that_already_holds_a_placeholder_changes_nothing(
    tmp_path: object,
) -> None:
    publisher = RecordingPublisher()
    filings = _filings([], HealthStatus.OK, "2024-01-02 is not the 1st of a month")
    ctx = _ctx(tmp_path, _finmind(), filings, publisher)

    first = await run_whole_market_job(MONTH_REVENUE_JOB, SECOND, ctx)
    written = ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", SECOND)
    second = await run_whole_market_job(MONTH_REVENUE_JOB, SECOND, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert second.rows_written == 0
    assert ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", SECOND) == written
    assert len(publisher.signals) == 1  # the second sweep wrote nothing and signalled nothing


async def test_rows_after_a_placeholder_are_written_and_signalled(tmp_path: object) -> None:
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, _finmind(), _filings([], HealthStatus.OK, "none"), publisher)
    await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    ctx = _ctx(tmp_path, _finmind(), _filings(), publisher)
    run = await run_whole_market_job(MONTH_REVENUE_JOB, FIRST, ctx)

    assert run.rows_written == 1
    assert _symbols(ctx) == ["1101"]
    assert len(publisher.signals) == 2


async def test_a_statement_job_on_a_date_that_is_not_a_quarter_end_asks_nobody_else(
    tmp_path: object,
) -> None:
    for job in (FINANCIALS_JOB, BALANCE_SHEET_JOB, CASH_FLOW_JOB):
        finmind = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
        filings = _filings([], HealthStatus.OK, "2024-01-02 is not a quarter end")
        ctx = _ctx(tmp_path, finmind, filings)

        run = await run_whole_market_job(job, SECOND, ctx)

        assert run.status is RunStatus.SUCCESS
        assert run.detail == f"served by {FILINGS_ID}: 2024-01-02 is not a quarter end"
        assert filings.calls[0][0] is job.capability
        assert finmind.calls == []


# --- a period that is not due yet, through the REAL filings adapter (no request is made: the
# adapter answers from the deadline calendar alone) -------------------------------------------

_Q3_END = date(2026, 9, 30)  # a Wednesday; its last statutory deadline is 2026-11-29
_TODAY = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _real_filings() -> CompanyFilingsAdapter:
    return CompanyFilingsAdapter(clock=lambda: _TODAY)


async def test_q3_statements_before_the_deadline_are_skipped_not_failed(tmp_path: object) -> None:
    """The stray failure of a backfill: the quarter that ended a month ago is not complete (MOPS
    says so without a request) and FinMind's free tier refuses. Nothing is wrong, so the day is
    SKIPPED with the deadline, and no marker keeps it from being retried."""
    for job in (FINANCIALS_JOB, BALANCE_SHEET_JOB, CASH_FLOW_JOB):
        finmind = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
        ctx = _ctx(tmp_path, finmind, _real_filings())

        run = await run_whole_market_job(job, _Q3_END, ctx)

        assert run.status is RunStatus.SKIPPED
        assert run.detail == "period not yet due: filing deadline 2026-11-29"
        assert run.rows_written == 0
        assert not ctx.store.is_closed(job.dataset, "TW", _Q3_END)  # no sticky closure marker
        assert not ctx.store.has_partition(job.dataset, "TW", _Q3_END)
        assert detect_missing_trading_days(job, ctx, start=_Q3_END, end=_Q3_END) == (_Q3_END,)


async def test_monthly_revenue_before_the_11th_is_skipped_not_failed(tmp_path: object) -> None:
    first = date(2026, 11, 2)  # October's revenue is due on 2026-11-10; the 1st is a Sunday
    finmind = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
    clock = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    ctx = _ctx(tmp_path, finmind, CompanyFilingsAdapter(clock=lambda: clock))

    run = await run_whole_market_job(MONTH_REVENUE_JOB, first, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "period not yet due: filing deadline 2026-11-10"
    assert not ctx.store.is_closed(MONTH_REVENUE_JOB.dataset, "TW", first)


async def test_a_period_not_due_is_still_served_by_a_later_source_that_has_it(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, _finmind(), _real_filings())

    run = await run_whole_market_job(MONTH_REVENUE_JOB, date(2026, 11, 2), ctx)

    assert run.status is RunStatus.SUCCESS  # a paid FinMind that has early filings still wins
    assert run.detail.startswith(f"served by {FINMIND_ID}")


async def test_a_skipped_period_stays_a_gap_for_the_next_backfill(
    tmp_path: object,
) -> None:
    refused = _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403")
    skipped = await run_whole_market_job(
        FINANCIALS_JOB, _Q3_END, _ctx(tmp_path, refused, _real_filings())
    )
    assert skipped.status is RunStatus.SKIPPED
    assert is_period_not_due(skipped.detail)

    # no marker and no placeholder: the gap is still listed, so the next backfill asks again
    ctx = _ctx(tmp_path, refused, _real_filings())
    assert detect_missing_trading_days(FINANCIALS_JOB, ctx, start=_Q3_END, end=_Q3_END) == (
        _Q3_END,
    )
