"""The roster job (`TaiwanStockInfo`, `STOCK_INFO_JOB`) through `run_whole_market_job`.

First, what it did with FinMind as its only source (unchanged by the source chain); then the
official roster asked first, and a roster already fetched today left alone.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

from ingest.context import IngestContext
from ingest.jobs import STOCK_INFO_JOB, run_whole_market_job
from ingest.jobs.source_chain import FINMIND_ID, ROSTER_ID, load_whole_market_priority
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.ingest.test_whole_market_runner import RecordingPublisher, StubSource

DAY = date(2026, 10, 27)  # a Tuesday
NEXT_DAY = date(2026, 10, 28)


def _row(symbol: str, category: str = "半導體業", as_of: str = "2026-10-27") -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "name": f"name {symbol}",
        "industry_category": category,
        "security_type": "twse",
        "as_of": as_of,
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
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub",
        payload={"rows": rows},
        health=SourceHealth(status=status, detail=detail),
    )


def _finmind(
    rows: list[dict[str, object]] | None = None,
    status: HealthStatus = HealthStatus.OK,
    detail: str = "ok",
) -> StubSource:
    return StubSource(_result([_row("2330")] if rows is None else rows, status, detail))


def _roster(
    rows: list[dict[str, object]] | None = None,
    status: HealthStatus = HealthStatus.OK,
    detail: str = "TWSE ISIN lists updated 2026-10-27: listed 1 + OTC 1 + emerging 1 securities",
) -> StubSource:
    return StubSource(
        _result([_row("1101")] if rows is None else rows, status, detail, "EXCHANGE_ROSTER")
    )


def _ctx(tmp_path: object, finmind: StubSource, roster: StubSource | None = None) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        roster=roster,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=RecordingPublisher(),
    )


def _symbols(ctx: IngestContext, run_date: date = DAY) -> list[str]:
    rows = ctx.store.read_partition(STOCK_INFO_JOB.dataset, "TW", run_date)
    return sorted(str(row["symbol"]) for row in rows)


# ------------------------------------------------------------- FinMind as the only source


async def test_with_only_finmind_the_roster_is_written_and_its_detail_kept(
    tmp_path: object,
) -> None:
    finmind = _finmind([_row("2330"), _row("2317", "電腦及週邊設備業")])
    ctx = _ctx(tmp_path, finmind)

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    assert run.detail == "ok"
    assert _symbols(ctx) == ["2317", "2330"]
    assert finmind.calls[0][0] is Capability.STOCK_INFO


async def test_with_only_finmind_a_refusal_is_a_failed_run(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403"))

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert run.detail == "FinMind HTTP 403"
    assert not ctx.store.has_partition(STOCK_INFO_JOB.dataset, "TW", DAY)


async def test_a_malformed_roster_row_is_dropped_before_the_write(tmp_path: object) -> None:
    bad = {**_row("9999"), "name": None}
    ctx = _ctx(tmp_path, _finmind([_row("2330"), bad]))

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.rows_written == 1
    assert _symbols(ctx) == ["2330"]


# --------------------------------------------------------- the official roster asked first


def test_the_configured_chain_asks_the_official_roster_before_finmind() -> None:
    chain = load_whole_market_priority()["TW"][Capability.STOCK_INFO.value]

    assert chain == [ROSTER_ID, FINMIND_ID]


async def test_the_official_roster_serves_and_finmind_is_not_asked(tmp_path: object) -> None:
    finmind, roster = _finmind(), _roster()
    ctx = _ctx(tmp_path, finmind, roster)

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == (
        f"served by {ROSTER_ID}: "
        "TWSE ISIN lists updated 2026-10-27: listed 1 + OTC 1 + emerging 1 securities"
    )
    assert _symbols(ctx) == ["1101"]
    assert roster.calls[0][0] is Capability.STOCK_INFO
    assert finmind.calls == []


async def test_a_failing_official_roster_falls_back_to_finmind_and_says_why(
    tmp_path: object,
) -> None:
    roster = _roster([], HealthStatus.UNREACHABLE, "OTC: isin.twse.com.tw: HTTP 500")
    ctx = _ctx(tmp_path, _finmind(), roster)

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert (
        run.detail
        == f"served by {FINMIND_ID}: ok (after {ROSTER_ID}: OTC: isin.twse.com.tw: HTTP 500)"
    )
    assert _symbols(ctx) == ["2330"]


async def test_both_failing_is_a_failed_run_with_both_reasons(tmp_path: object) -> None:
    roster = _roster([], HealthStatus.UNREACHABLE, "listed: HTTP 500")
    ctx = _ctx(tmp_path, _finmind([], HealthStatus.UNREACHABLE, "FinMind HTTP 403"), roster)

    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert run.detail == (
        f"no source served: {ROSTER_ID}: listed: HTTP 500; {FINMIND_ID}: FinMind HTTP 403"
    )
    assert not ctx.store.has_partition(STOCK_INFO_JOB.dataset, "TW", DAY)


# ------------------------------------------------------------ a fresh roster is not re-fetched


async def test_a_roster_already_fetched_today_is_not_asked_for_again(tmp_path: object) -> None:
    roster = _roster()
    ctx = _ctx(tmp_path, _finmind(), roster)
    await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)
    written = ctx.store.partition_files(STOCK_INFO_JOB.dataset, "TW", DAY)

    again = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert again.status is RunStatus.SUCCESS
    assert again.rows_written == 0
    assert again.detail == "already fetched for 2026-10-27: not asked again today"
    assert len(roster.calls) == 1
    assert ctx.store.partition_files(STOCK_INFO_JOB.dataset, "TW", DAY) == written


async def test_the_next_day_fetches_the_roster_again(tmp_path: object) -> None:
    roster = _roster()
    ctx = _ctx(tmp_path, _finmind(), roster)
    await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    run = await run_whole_market_job(STOCK_INFO_JOB, NEXT_DAY, ctx)

    assert run.rows_written == 1
    assert len(roster.calls) == 2


async def test_a_failed_attempt_today_is_retried(tmp_path: object) -> None:
    failing = _roster([], HealthStatus.UNREACHABLE, "listed: HTTP 500")
    await run_whole_market_job(
        STOCK_INFO_JOB, DAY, _ctx(tmp_path, _finmind([], HealthStatus.UNREACHABLE, "403"), failing)
    )

    ctx = _ctx(tmp_path, _finmind(), _roster())
    run = await run_whole_market_job(STOCK_INFO_JOB, DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
