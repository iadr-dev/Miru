"""After an ingest the screener's whole-market reads that this process already uses are recomputed
in the background, so the first request of the new data day does not pay for the read."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analytics.screener import engine
from analytics.screener.engine import _bulk_by_day, refresh_cached_bulk_reads
from api import screener_warm
from api.screener_warm import keep_screener_warm
from events.bus import Event
from ingest.store import ParquetStore

DAY1, DAY2 = date(2026, 10, 14), date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _clean_bulk_cache() -> None:
    engine._BULK_CACHE.clear()


def _signal(market: str = "TW") -> Event:
    return Event(
        kind="data.updated",
        seq=0,
        ts=datetime.now(UTC),
        data={"capability": "price_history", "market": market, "as_of": "x", "scope": "whole"},
    )


async def test_a_cached_read_is_recomputed_when_its_data_day_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetStore(root=tmp_path)
    current = {"day": DAY1}
    reads: list[date] = []

    def as_of(_store: ParquetStore, _market: str) -> date:
        return current["day"]

    def read(_store: ParquetStore, _market: str) -> dict[str, dict[str, float]]:
        reads.append(current["day"])
        return {"2330": {"rsi_14": float(current["day"].day)}}

    monkeypatch.setitem(engine._BULK_SOURCES, "technical", (as_of, read))
    await _bulk_by_day("technical", "TW", DAY1, lambda: read(store, "TW"))
    assert reads == [DAY1]

    assert await refresh_cached_bulk_reads(store) == 0  # same day: nothing to do
    current["day"] = DAY2
    assert await refresh_cached_bulk_reads(store) == 1
    assert reads == [DAY1, DAY2]

    # the next request finds day 2 ready: no further read
    value = await _bulk_by_day("technical", "TW", DAY2, lambda: read(store, "TW"))
    assert value == {"2330": {"rsi_14": 15.0}}
    assert reads == [DAY1, DAY2]


async def test_a_read_nobody_asked_for_is_not_warmed(tmp_path: Path) -> None:
    assert await refresh_cached_bulk_reads(ParquetStore(root=tmp_path)) == 0
    assert engine._BULK_CACHE == {}


async def _run_warm(
    events: list[Event],
    refreshes: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_refresh(_store: ParquetStore) -> int:
        refreshes.append(1)
        return 1

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", fake_refresh)

    async def updates() -> AsyncIterator[Event]:
        for event in events:
            yield event
        await asyncio.sleep(3600)  # the feed stays open

    async def no_wait(_seconds: float) -> None:
        await asyncio.sleep(0)

    task = asyncio.create_task(
        keep_screener_warm(
            updates,
            ParquetStore(root=Path()),
            settle_seconds=0,
            spread_seconds=0,
            sleep=no_wait,
            spread=lambda: 0.0,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_burst_of_tw_signals_is_one_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshes: list[int] = []

    await _run_warm([_signal() for _ in range(5)], refreshes, monkeypatch)

    assert len(refreshes) == 1


async def test_a_us_signal_does_not_warm_the_tw_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshes: list[int] = []

    await _run_warm([_signal("US")], refreshes, monkeypatch)

    assert refreshes == []


async def test_a_failed_refresh_does_not_stop_the_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def broken(_store: ParquetStore) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("disk gone")

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", broken)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await asyncio.sleep(3600)

    async def no_wait(_seconds: float) -> None:
        await asyncio.sleep(0)

    task = asyncio.create_task(
        keep_screener_warm(
            updates, ParquetStore(root=Path()), settle_seconds=0, spread_seconds=0, sleep=no_wait
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)

    assert calls == 1
    assert not task.done()  # still listening
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
