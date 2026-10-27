"""The ordered source chain of `run_whole_market_job`: official exchange reports first, FinMind
only when they fail or have nothing; gates, validation and journaling as for a single source."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

from ingest.context import IngestContext
from ingest.jobs import FINANCIALS_JOB, PRICE_EOD_JOB, run_whole_market_job
from ingest.jobs.source_chain import (
    EXCHANGE_ID,
    FINMIND_ID,
    MARKET_CLOSED_DETAIL,
    fetch_whole_market_chained,
    load_whole_market_priority,
    resolve_chain,
    taipei_today,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, Market, TLSPolicy
from schemas.source import HealthStatus
from sources.base import NO_DATA_PUBLISHED, ROUND_LOT_VOLUME
from tests.ingest.test_whole_market_runner import (
    TRADING_DAY,
    RecordingPublisher,
    StubSource,
    _bar,
    _result,
)

OFFICIAL_DETAIL = "TWSE 1 + TPEx 1 rows"


def _official(
    rows: list[dict[str, object]] | None = None,
    *,
    status: HealthStatus = HealthStatus.OK,
    detail: str = OFFICIAL_DETAIL,
) -> StubSource:
    return StubSource(
        _result(
            [_bar("1101")] if rows is None else rows,
            status=status,
            detail=detail,
            source_id="EXCHANGE_OFFICIAL",
        )
    )


def _finmind(
    rows: list[dict[str, object]] | None = None,
    *,
    status: HealthStatus = HealthStatus.OK,
    detail: str = "ok",
) -> StubSource:
    return StubSource(
        _result([_bar("2330")] if rows is None else rows, status=status, detail=detail)
    )


def _ctx(
    tmp_path: object,
    finmind: StubSource,
    exchange: StubSource | None,
    *,
    leader: object = None,
    publisher: RecordingPublisher | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        exchange=exchange,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher or RecordingPublisher(),
    )


def _symbols(ctx: IngestContext) -> list[str]:
    rows = ctx.store.read_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
    return sorted(str(r["symbol"]) for r in rows)


async def test_official_source_serves_and_finmind_is_never_called(tmp_path: object) -> None:
    finmind, official = _finmind(), _official()
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == f"served by {EXCHANGE_ID}: {OFFICIAL_DETAIL}"
    assert _symbols(ctx) == ["1101"]
    assert len(official.calls) == 1
    assert finmind.calls == []
    assert official.calls[0][1]["start_date"] == "2024-01-02"


async def test_official_empty_falls_back_to_finmind(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.STALE, detail="no rows published")
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == f"served by {FINMIND_ID}: ok (after {EXCHANGE_ID}: no rows)"
    assert _symbols(ctx) == ["2330"]
    assert len(official.calls) == 1
    assert len(finmind.calls) == 1


async def test_official_error_falls_back_to_finmind_and_says_why(tmp_path: object) -> None:
    official = _official(
        [], status=HealthStatus.UNREACHABLE, detail="TWSE: www.twse.com.tw: HTTP 403"
    )
    ctx = _ctx(tmp_path, _finmind(), official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == (
        f"served by {FINMIND_ID}: ok (after {EXCHANGE_ID}: TWSE: www.twse.com.tw: HTTP 403)"
    )
    assert _symbols(ctx) == ["2330"]


async def test_both_failing_journals_failed_with_both_reasons(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.UNREACHABLE, detail="TPEx: HTTP 503")
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert run.rows_written == 0
    assert run.detail == (
        f"no source served: {EXCHANGE_ID}: TPEx: HTTP 503; {FINMIND_ID}: FinMind HTTP 403"
    )
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_official_empty_and_finmind_failing_is_failed_not_an_empty_day(
    tmp_path: object,
) -> None:
    official = _official([], status=HealthStatus.STALE, detail="no rows published")
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert FINMIND_ID in run.detail
    assert EXCHANGE_ID in run.detail
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_every_source_empty_is_an_empty_trading_day(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.STALE, detail="no rows published")
    finmind = _finmind([], status=HealthStatus.STALE, detail="no rows returned")
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 0
    assert run.detail == f"no rows from {EXCHANGE_ID}, {FINMIND_ID}"
    assert ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_rows_from_the_official_source_are_validated_like_any_other(
    tmp_path: object,
) -> None:
    official = _official([_bar("1101"), _bar("9999", close=None)])
    ctx = _ctx(tmp_path, _finmind(), official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.rows_written == 1
    assert _symbols(ctx) == ["1101"]


async def test_publish_signal_is_unchanged_by_the_serving_source(tmp_path: object) -> None:
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, _finmind(), _official(), publisher=publisher)

    await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert publisher.signals == [(Capability.PRICE_EOD, "2024-01-02")]


async def test_non_trading_day_asks_no_source(tmp_path: object) -> None:
    finmind, official = _finmind(), _official()
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, date(2024, 1, 6), ctx)

    assert run.status is RunStatus.SKIPPED
    assert finmind.calls == []
    assert official.calls == []


async def test_non_leader_asks_no_source(tmp_path: object) -> None:
    finmind, official = _finmind(), _official()
    ctx = _ctx(tmp_path, finmind, official, leader=NeverLeader())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert finmind.calls == []
    assert official.calls == []


async def test_a_dataset_the_exchanges_do_not_cover_goes_straight_to_finmind(
    tmp_path: object,
) -> None:
    finmind, official = _finmind([]), _official()
    ctx = _ctx(tmp_path, finmind, official)

    await run_whole_market_job(FINANCIALS_JOB, TRADING_DAY, ctx)

    assert official.calls == []
    assert len(finmind.calls) == 1


async def test_without_an_exchange_source_the_finmind_path_is_exactly_as_before(
    tmp_path: object,
) -> None:
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, None)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.detail == "ok"
    assert [link.source_id for link in resolve_chain(ctx, Capability.PRICE_EOD)] == [FINMIND_ID]


def test_the_priority_is_data_with_the_official_source_first() -> None:
    table = load_whole_market_priority()["TW"]

    for capability in ("price_eod", "institutional_flow", "margin_short", "valuation_ratios"):
        assert table[capability] == [EXCHANGE_ID, FINMIND_ID]
    assert {c.value for c in Capability} >= set(table)


def test_chain_follows_the_configured_order(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, _finmind(), _official())

    ids = [link.source_id for link in resolve_chain(ctx, Capability.MARGIN_SHORT, Market.TW)]

    assert ids == [EXCHANGE_ID, FINMIND_ID]


async def test_derived_batches_use_the_same_chain(tmp_path: object) -> None:
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, _official())

    result = await fetch_whole_market_chained(ctx, Capability.MARGIN_SHORT, TRADING_DAY)

    assert result.source_id == "EXCHANGE_OFFICIAL"
    assert finmind.calls == []


async def test_derived_batches_see_the_last_failure_when_nothing_serves(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.UNREACHABLE, detail="TPEx: HTTP 503")
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, official)

    result = await fetch_whole_market_chained(ctx, Capability.MARGIN_SHORT, TRADING_DAY)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "FinMind HTTP 403"


# ---------------------------------------------------------------- exchange-confirmed closures


def _closed() -> StubSource:
    return _official([], status=HealthStatus.STALE, detail=NO_DATA_PUBLISHED)


async def test_both_exchanges_explicitly_empty_on_a_past_date_is_a_skipped_closure(
    tmp_path: object,
) -> None:
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="HTTP 400")
    ctx = _ctx(tmp_path, finmind, _closed())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == MARKET_CLOSED_DETAIL
    assert run.rows_written == 0
    assert finmind.calls == []  # FinMind is never asked
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
    assert ctx.store.is_closed(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)
    # per dataset: another dataset's gap check is not suppressed
    assert not ctx.store.is_closed(FINANCIALS_JOB.dataset, "TW", TRADING_DAY)


async def test_an_explicit_empty_plus_an_exchange_error_still_falls_back(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.UNREACHABLE, detail="TPEx: HTTP 503")
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert len(finmind.calls) == 1
    assert not ctx.store.is_closed(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_an_empty_answer_that_is_not_explicit_falls_back(tmp_path: object) -> None:
    official = _official([], status=HealthStatus.STALE, detail="no rows published (other)")
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert len(finmind.calls) == 1


async def test_explicit_empty_today_is_not_a_closure_yet(tmp_path: object) -> None:
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="HTTP 400")
    ctx = _ctx(tmp_path, finmind, _closed())

    run = await run_whole_market_job(PRICE_EOD_JOB, taipei_today(), ctx)

    assert run.status is RunStatus.FAILED  # not published yet: retried later
    assert len(finmind.calls) == 1
    assert not ctx.store.is_closed(PRICE_EOD_JOB.dataset, "TW", taipei_today())


async def test_finmind_alone_never_declares_a_closure(tmp_path: object) -> None:
    finmind = _finmind([], status=HealthStatus.STALE, detail=NO_DATA_PUBLISHED)
    ctx = _ctx(tmp_path, finmind, None)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS  # the old empty-day behaviour
    assert not ctx.store.is_closed(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


async def test_a_later_successful_write_clears_the_closure(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, _finmind(), _official())
    ctx.store.mark_closed(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY, MARKET_CLOSED_DETAIL)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert not ctx.store.is_closed(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY)


# -------------------------------------------- round-lot-volume rows (TPEx all-session report down)

ROUND_LOT_DETAIL = f"TWSE 1 + TPEx 1 rows, {ROUND_LOT_VOLUME} (TPEx: dailyQuotes: HTTP 403)"


def _round_lot_official() -> StubSource:
    return _official(status=HealthStatus.UNREACHABLE, detail=ROUND_LOT_DETAIL)


async def test_round_lot_answer_falls_through_to_finmind_when_finmind_serves(
    tmp_path: object,
) -> None:
    finmind = _finmind()
    ctx = _ctx(tmp_path, finmind, _round_lot_official())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == f"served by {FINMIND_ID}: ok (after {EXCHANGE_ID}: {ROUND_LOT_DETAIL})"
    assert _symbols(ctx) == ["2330"]  # FinMind's complete rows, not the exchange's
    assert len(finmind.calls) == 1


async def test_round_lot_answer_is_served_tagged_when_finmind_also_fails(tmp_path: object) -> None:
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, _round_lot_official())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
    assert run.detail == (
        f"served by {EXCHANGE_ID}: {ROUND_LOT_DETAIL} "
        f"(no complete source: {FINMIND_ID}: FinMind HTTP 403)"
    )
    assert ROUND_LOT_VOLUME in run.detail
    assert _symbols(ctx) == ["1101"]


async def test_round_lot_answer_is_served_tagged_when_finmind_has_nothing(
    tmp_path: object,
) -> None:
    finmind = _finmind([], status=HealthStatus.STALE, detail="no rows published")
    ctx = _ctx(tmp_path, finmind, _round_lot_official())

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ROUND_LOT_VOLUME in run.detail
    assert f"(no complete source: {FINMIND_ID}: no rows)" in run.detail
    assert _symbols(ctx) == ["1101"]


async def test_a_failed_answer_without_the_tag_is_never_served_for_its_rows(
    tmp_path: object,
) -> None:
    official = _official(status=HealthStatus.UNREACHABLE, detail="TPEx: HTTP 503")  # has rows
    finmind = _finmind([], status=HealthStatus.UNREACHABLE, detail="FinMind HTTP 403")
    ctx = _ctx(tmp_path, finmind, official)

    run = await run_whole_market_job(PRICE_EOD_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED
    assert run.rows_written == 0
