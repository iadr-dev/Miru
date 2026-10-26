"""`run_whole_market_job` characterization: the FinMind-only path, driven by a stub source.

Pins what the shared runner does with one source's result (success, empty day, failure, gates,
validation), so the source-chain change keeps every one of these semantics byte-for-byte.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

from ingest.context import IngestContext
from ingest.jobs import PRICE_EOD_JOB, run_whole_market_job
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult

TRADING_DAY = date(2024, 1, 2)  # a Tuesday


def _bar(symbol: str = "2330", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "market": "TW",
        "trade_date": TRADING_DAY.isoformat(),
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 104.0,
        "volume": 1000,
        "turnover": 104000,
    }
    row.update(overrides)
    return row


def _result(
    rows: list[dict[str, object]],
    *,
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
        payload={"bars": rows},
        health=SourceHealth(status=status, detail=detail),
    )


class StubSource:
    """Duck-types `fetch_whole_market`, records the calls."""

    def __init__(self, result: SourceResult) -> None:
        self.result = result
        self.calls: list[tuple[Capability, dict[str, object]]] = []

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        self.calls.append((cap, {"tls_policy": tls_policy, **params}))
        return self.result


class RecordingPublisher:
    def __init__(self) -> None:
        self.signals: list[tuple[Capability, str]] = []

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        self.signals.append((capability, as_of))


def _ctx(
    tmp_path: object,
    source: StubSource,
    *,
    leader: object = None,
    publisher: RecordingPublisher | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=source,  # type: ignore[arg-type]
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher or RecordingPublisher(),
    )


async def test_success_writes_rows_and_journals_the_source_detail_verbatim(
    tmp_path: object,
) -> None:
    source = StubSource(_result([_bar("2330"), _bar("2317")]))
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, source, publisher=publisher)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    assert run.detail == "ok"
    assert source.calls == [
        (Capability.PRICE_EOD, {"tls_policy": TLSPolicy.STRICT, "start_date": "2024-01-02"})
    ]
    stored = ctx.store.read_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
    assert sorted(str(r["symbol"]) for r in stored) == ["2317", "2330"]
    assert publisher.signals == [(Capability.PRICE_EOD, "2024-01-02")]


async def test_empty_healthy_trading_day_is_success_with_zero_rows(tmp_path: object) -> None:
    source = StubSource(
        _result([], status=HealthStatus.STALE, detail="no rows returned for the requested range")
    )
    ctx = _ctx(tmp_path, source)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 0
    assert ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_failed_fetch_journals_failed_with_the_source_reason_and_writes_nothing(
    tmp_path: object,
) -> None:
    source = StubSource(_result([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403"))
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, source, publisher=publisher)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert run.rows_written == 0
    assert run.detail == "FinMind HTTP 403"
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
    assert publisher.signals == []


async def test_tier_refusal_is_a_failure_too(tmp_path: object) -> None:
    source = StubSource(_result([], status=HealthStatus.UNAVAILABLE_TIER, detail="tier"))
    ctx = _ctx(tmp_path, source)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED


async def test_malformed_rows_are_dropped_before_the_write(tmp_path: object) -> None:
    bad = _bar("9999", close=None)
    source = StubSource(_result([_bar("2330"), bad]))
    ctx = _ctx(tmp_path, source)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1


async def test_non_trading_day_skips_without_calling_the_source(tmp_path: object) -> None:
    source = StubSource(_result([_bar()]))
    ctx = _ctx(tmp_path, source)

    run = await run_whole_market_job(PRICE_EOD_JOB, date(2024, 1, 6), ctx)  # a Saturday

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "non-trading day (calendar-gated)"
    assert source.calls == []


async def test_non_leader_skips_without_calling_the_source(tmp_path: object) -> None:
    source = StubSource(_result([_bar()]))
    ctx = _ctx(tmp_path, source, leader=NeverLeader())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "not the leader — write skipped"
    assert source.calls == []
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
