"""Gap-fill / backfill tests."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.backfill import backfill_range, detect_missing_trading_days
from ingest.context import IngestContext
from ingest.jobs import PRICE_EOD_JOB
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _ctx(tmp_path: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


async def test_backfill_range_fills_only_missing_trading_days(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    ctx = _ctx(tmp_path)
    # 2024-01-01 is a Monday, 2024-01-02 Tue, 2024-01-03 Wed — 3 trading days.
    start, end = date(2024, 1, 1), date(2024, 1, 3)

    missing_before = detect_missing_trading_days(PRICE_EOD_JOB, ctx, start=start, end=end)
    assert missing_before == (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))

    runs = await backfill_range(PRICE_EOD_JOB, ctx, start=start, end=end)

    assert len(runs) == 3
    assert all(r.status is RunStatus.SUCCESS for r in runs)
    missing_after = detect_missing_trading_days(PRICE_EOD_JOB, ctx, start=start, end=end)
    assert missing_after == ()


async def test_backfill_range_skips_dates_already_partitioned(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    ctx = _ctx(tmp_path)
    already_written = date(2024, 1, 2)
    ctx.store.write_partition(PRICE_EOD_JOB.dataset, "TW", already_written, [{"symbol": "2330"}])

    calls: list[str] = []

    def _tracking_factory(**kwargs: object) -> object:
        calls.append("called")
        return make_json_client_factory(body)(**kwargs)

    monkeypatch.setattr(finmind_module, "build_async_client", _tracking_factory)

    runs = await backfill_range(
        PRICE_EOD_JOB, ctx, start=already_written, end=already_written, only_missing=True
    )

    assert runs == ()  # nothing to do — already partitioned
    assert not calls  # no fetch attempted for an already-filled date
