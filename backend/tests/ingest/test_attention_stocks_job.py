"""`job_attention_stocks` - the daily attention snapshot, cut from the exchanges' history reports.

The OpenAPI `announcement/notice` dump the job used to read answers only its empty placeholder
(`tests/fixtures/twse_openapi/announcement_notice.json`), so the stored snapshots held no listed
flag at all. The job now asks both exchanges' range reports for the flags of the day and of the
trading day before it, the rule `ingest/event_history.py` rebuilds the past with. The sources are
a stub here (the real answers are frozen in `tests/sources/test_exchange_event_history.py`).
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest

import ingest.jobs.risk_lists as jobs_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.event_history import backfill_event_history
from ingest.jobs import ATTENTION_STOCKS_DATASET, job_attention_stocks
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.exchange_event_history import EventHistory
from sources.exchange_whole_market._common import ExchangeFetchError
from sources.finmind import FinMindAdapter
from tests.ingest.test_event_history import StubSource, _flag

MARKET = "TW"
MONDAY = date(2026, 9, 14)
FRIDAY = date(2026, 9, 11)


def _ctx(tmp_path: Path, *, leader: object = None) -> IngestContext:
    settings = Settings(_env_file=None, finmind_api_key="test-token")  # type: ignore[call-arg,arg-type]
    return IngestContext(
        finmind=FinMindAdapter(settings=settings),
        store=ParquetStore(root=tmp_path),
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _week() -> list[dict[str, object]]:
    return [
        _flag("3374", "2026-09-10", "tpex"),  # two trading days back: not in Monday's list
        _flag("3081", "2026-09-11", "twse"),
        _flag("6173", "2026-09-11", "tpex"),
        _flag("2330", "2026-09-14", "twse"),
        _flag("3441", "2026-09-14", "tpex"),
    ]


def _use(monkeypatch: pytest.MonkeyPatch, source: object) -> None:
    monkeypatch.setattr(jobs_module, "new_event_history_adapter", lambda: source)


def _stored(ctx: IngestContext, day: date = MONDAY) -> list[dict[str, object]]:
    return [
        {k: v for k, v in row.items() if k not in {"dataset", "date", "v"}}
        for row in ctx.store.read_partition(ATTENTION_STOCKS_DATASET, MARKET, day)
    ]


class FailingSource(StubSource):
    """One exchange cannot be read: the adapter fails the whole range (never half a market)."""

    async def fetch_attention(
        self, start: date, end: date, *, tls_policy: TLSPolicy
    ) -> EventHistory:
        self.calls.append(("attention", start, end))
        raise ExchangeFetchError("TPEx: response is not JSON after 2 tries")


async def test_the_snapshot_holds_the_listed_and_the_otc_flags_of_the_day_and_the_day_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = StubSource(flags=_week())
    _use(monkeypatch, source)
    ctx = _ctx(tmp_path)

    run = await job_attention_stocks(MONDAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert source.calls == [("attention", FRIDAY, MONDAY)]
    rows = _stored(ctx)
    assert run.rows_written == len(rows) == 4
    assert {(r["security_type"], r["symbol"], r["flagged_date"]) for r in rows} == {
        ("twse", "3081", "2026-09-11"),
        ("tpex", "6173", "2026-09-11"),
        ("twse", "2330", "2026-09-14"),
        ("tpex", "3441", "2026-09-14"),
    }


async def test_the_live_snapshot_is_the_snapshot_the_history_rebuild_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = StubSource(flags=_week())
    _use(monkeypatch, source)
    live, rebuilt = _ctx(tmp_path / "live"), _ctx(tmp_path / "rebuilt")

    await job_attention_stocks(MONDAY, live)
    await backfill_event_history(
        rebuilt, start=MONDAY, end=MONDAY, adapter=source.as_adapter(), today=date(2026, 10, 27)
    )

    assert _stored(live) == _stored(rebuilt)
    assert len(_stored(live)) == 4


async def test_an_exchange_that_cannot_be_read_writes_nothing_and_the_next_run_fills_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, FailingSource(flags=_week()))

    failed = await job_attention_stocks(MONDAY, ctx)

    assert failed.status is RunStatus.FAILED
    assert "TPEx" in failed.detail
    assert not ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, MONDAY)

    _use(monkeypatch, StubSource(flags=_week()))
    retried = await job_attention_stocks(MONDAY, ctx)

    assert retried.status is RunStatus.SUCCESS
    assert len(_stored(ctx)) == 4


async def test_a_failed_run_leaves_the_stored_snapshot_of_the_day_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, StubSource(flags=_week()))
    await job_attention_stocks(MONDAY, ctx)
    before = _stored(ctx)
    _use(monkeypatch, FailingSource())

    run = await job_attention_stocks(MONDAY, ctx)

    assert run.status is RunStatus.FAILED
    assert _stored(ctx) == before


@pytest.mark.parametrize("behind", ["twse", "tpex"])
async def test_a_day_one_exchange_has_not_posted_yet_is_not_written_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behind: str
) -> None:
    """Evening of the day: one report already lists today, the other still ends yesterday."""
    flags = [
        f
        for f in _week()
        if not (f["security_type"] == behind and f["flagged_date"] == "2026-09-14")
    ]
    _use(monkeypatch, StubSource(flags=flags))
    ctx = _ctx(tmp_path)

    run = await job_attention_stocks(MONDAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert behind in run.detail
    assert run.rows_written == 0
    assert not ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, MONDAY)


async def test_nothing_posted_for_either_exchange_is_a_skipped_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, StubSource())
    ctx = _ctx(tmp_path)

    run = await job_attention_stocks(MONDAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, MONDAY)


async def test_a_second_run_rewrites_the_same_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, StubSource(flags=_week()))
    ctx = _ctx(tmp_path)

    first = await job_attention_stocks(MONDAY, ctx)
    second = await job_attention_stocks(MONDAY, ctx)

    assert first.status is second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written == 4


async def test_a_row_the_stored_model_rejects_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = {"symbol": "0000", "security_type": "twse", "flagged_date": "2026-09-14"}
    _use(monkeypatch, StubSource(flags=[*_week(), bad]))
    ctx = _ctx(tmp_path)

    run = await job_attention_stocks(MONDAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 4


async def test_no_fetch_without_the_lease_or_on_a_closed_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = StubSource(flags=_week())
    _use(monkeypatch, source)

    follower = await job_attention_stocks(MONDAY, _ctx(tmp_path / "a", leader=NeverLeader()))
    saturday = await job_attention_stocks(date(2026, 9, 12), _ctx(tmp_path / "b"))

    assert follower.status is saturday.status is RunStatus.SKIPPED
    assert source.calls == []
