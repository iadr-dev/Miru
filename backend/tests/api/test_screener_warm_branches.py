"""Characterization of `keep_screener_warm`: feed reopening, the delay arithmetic, re-arming after
a refresh starts, logging, and cancelling a not-yet-started refresh on shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from api import screener_warm
from api.screener_warm import keep_screener_warm
from events.bus import Event
from ingest.store import ParquetStore


def _signal(market: str = "TW") -> Event:
    return Event(
        kind="data.updated",
        seq=0,
        ts=datetime.now(UTC),
        data={"capability": "price_history", "market": market},
    )


async def _spin(ticks: int = 40) -> None:
    for _ in range(ticks):
        await asyncio.sleep(0)


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_dropped_feed_is_logged_and_reopened_after_the_retry_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opens = 0
    sleeps: list[float] = []

    async def updates() -> AsyncIterator[Event]:
        nonlocal opens
        opens += 1
        if opens == 1:
            raise ConnectionError("feed dropped")
        yield _signal("US")
        await asyncio.sleep(3600)

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    with capture_logs() as logs:
        task = asyncio.create_task(
            keep_screener_warm(updates, ParquetStore(root=Path()), sleep=record_sleep)
        )
        await _spin()
        await _stop(task)

    assert opens == 2
    assert sleeps[0] == 5.0
    assert [(log["event"], log["log_level"]) for log in logs] == [
        ("screener_warm_feed_dropped", "warning")
    ]


async def test_a_feed_that_ends_normally_is_reopened_after_the_retry_pause() -> None:
    opens = 0
    sleeps: list[float] = []

    async def updates() -> AsyncIterator[Event]:
        nonlocal opens
        opens += 1
        return
        yield  # pragma: no cover - makes this an async generator

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    task = asyncio.create_task(
        keep_screener_warm(updates, ParquetStore(root=Path()), sleep=record_sleep)
    )
    await _spin()
    await _stop(task)

    assert opens > 2
    assert set(sleeps) == {5.0}


async def test_the_refresh_waits_settle_plus_a_spread_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_refresh(_store: ParquetStore) -> int:
        return 0

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", fake_refresh)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await asyncio.sleep(3600)

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    task = asyncio.create_task(
        keep_screener_warm(
            updates,
            ParquetStore(root=Path()),
            settle_seconds=10.0,
            spread_seconds=20.0,
            sleep=record_sleep,
            spread=lambda: 0.5,
        )
    )
    await _spin()
    await _stop(task)

    assert sleeps == [20.0]


async def test_a_signal_after_the_refresh_has_started_schedules_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes = 0
    release = asyncio.Event()

    async def fake_refresh(_store: ParquetStore) -> int:
        nonlocal refreshes
        refreshes += 1
        await release.wait()  # the refresh is running when the next signal arrives
        return 2

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", fake_refresh)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await _spin(10)
        yield _signal()
        yield _signal()  # covered by the second scheduled refresh
        await asyncio.sleep(3600)

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
    await _spin()
    release.set()
    await _spin()
    await _stop(task)

    assert refreshes == 2


async def test_a_successful_refresh_is_logged_with_the_read_count_and_zero_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([3, 0])

    async def fake_refresh(_store: ParquetStore) -> int:
        return next(results)

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", fake_refresh)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await _spin(10)
        yield _signal()
        await asyncio.sleep(3600)

    async def no_wait(_seconds: float) -> None:
        await asyncio.sleep(0)

    with capture_logs() as logs:
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
        await _spin()
        await _stop(task)

    assert [(log["event"], log.get("reads")) for log in logs] == [("screener_warmed", 3)]


async def test_a_failed_refresh_is_logged_as_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_store: ParquetStore) -> int:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", broken)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await asyncio.sleep(3600)

    async def no_wait(_seconds: float) -> None:
        await asyncio.sleep(0)

    with capture_logs() as logs:
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
        await _spin()
        await _stop(task)

    assert [(log["event"], log["log_level"]) for log in logs] == [
        ("screener_warm_failed", "warning")
    ]


async def test_cancelling_the_listener_cancels_a_refresh_still_waiting_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = False
    pending_cancelled = False

    async def fake_refresh(_store: ParquetStore) -> int:
        nonlocal refreshed
        refreshed = True
        return 1

    monkeypatch.setattr(screener_warm, "refresh_cached_bulk_reads", fake_refresh)

    async def updates() -> AsyncIterator[Event]:
        yield _signal()
        await asyncio.sleep(3600)

    async def settle_forever(seconds: float) -> None:
        nonlocal pending_cancelled
        if seconds == 7.0:  # the refresh's settle wait
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pending_cancelled = True
                raise
        await asyncio.sleep(0)

    task = asyncio.create_task(
        keep_screener_warm(
            updates,
            ParquetStore(root=Path()),
            settle_seconds=7.0,
            spread_seconds=0,
            sleep=settle_forever,
            spread=lambda: 0.0,
        )
    )
    await _spin()
    await _stop(task)
    await _spin()

    assert pending_cancelled
    assert not refreshed
