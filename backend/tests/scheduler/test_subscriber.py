"""The live `data.updated` subscriber: signal -> evaluation, resilience to bad events and dropped
subscriptions, and that a signal racing the reconciliation sweep can never double-notify."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from events.bus import Event
from scheduler.subscriber import consume_updates, parse_update, run_subscriber
from scheduler.templates import ClosePrice
from schemas.enums import Capability, Market, Tier
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_alert_engine import (
    PRICE,
    TODAY,
    FakeReader,
    bar,
    create_rule,
    make_worker,
    notifications,
)


def update_event(**data: object) -> Event:
    return Event(kind="data.updated", seq=0, ts=datetime.now(UTC), data=dict(data))


def price_update(as_of: str = TODAY.isoformat()) -> Event:
    return update_event(capability="price_eod", market="TW", as_of=as_of, scope="whole_market")


async def stream(*events: Event) -> AsyncIterator[Event]:
    for event in events:
        yield event


class RecordingHandler:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[Capability, Market, str]] = []
        self._fail_on = fail_on

    async def handle_data_updated(
        self, *, capability: Capability, market: Market, as_of: str
    ) -> list[str]:
        self.calls.append((capability, market, as_of))
        if as_of == self._fail_on:
            raise RuntimeError("boom")
        return []


def test_parse_update_accepts_a_well_formed_signal() -> None:
    assert parse_update(price_update("2026-10-02")) == (
        Capability.PRICE_EOD,
        Market.TW,
        "2026-10-02",
    )


def test_parse_update_skips_other_kinds_and_malformed_payloads() -> None:
    assert parse_update(Event(kind="quote.tick", seq=0, ts=datetime.now(UTC), data={})) is None
    assert parse_update(update_event(capability="price_eod", market="TW")) is None  # no as_of
    assert parse_update(update_event(capability="nope", market="TW", as_of="x")) is None
    assert parse_update(update_event(capability="price_eod", market="ZZ", as_of="x")) is None


async def test_consume_skips_bad_events_and_survives_a_failing_handler() -> None:
    handler = RecordingHandler(fail_on="2026-10-02")
    await consume_updates(
        handler,
        stream(
            update_event(garbage=True),
            price_update("2026-10-02"),  # handler raises
            price_update("2026-10-03"),  # must still be processed afterwards
        ),
    )
    assert [c[2] for c in handler.calls] == ["2026-10-02", "2026-10-03"]


async def test_signal_triggers_evaluation_end_to_end(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    reader.put(PRICE, TODAY, [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    await consume_updates(worker, stream(price_update()))
    notes = await notifications(session_factory, user)
    assert [n.source_id for n in notes] == [rule.id]


async def test_signal_racing_the_sweep_notifies_once(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    reader.put(PRICE, TODAY, [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    # The signal, a duplicate signal, and the sweep all land for the same as_of, concurrently.
    await asyncio.gather(
        consume_updates(worker, stream(price_update(), price_update())),
        worker.reconcile_alerts(),
    )
    assert len(await notifications(session_factory, user)) == 1


async def test_run_subscriber_resubscribes_after_the_stream_drops() -> None:
    handler = RecordingHandler()
    stop = asyncio.Event()
    opened = 0

    def source() -> AsyncIterator[Event]:
        nonlocal opened
        opened += 1
        if opened == 1:

            async def broken() -> AsyncIterator[Event]:
                raise ConnectionError("redis went away")
                yield  # pragma: no cover - makes this an async generator

            return broken()
        stop.set()  # the second (healthy) subscription delivers one event, then we stop
        return stream(price_update("2026-10-05"))

    await asyncio.wait_for(run_subscriber(handler, source, stop=stop, retry_delay=0.01), timeout=5)
    assert opened == 2
    assert [c[2] for c in handler.calls] == ["2026-10-05"]


async def test_run_subscriber_exits_promptly_when_already_stopped() -> None:
    handler = RecordingHandler()
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(run_subscriber(handler, stream, stop=stop), timeout=2)
    assert handler.calls == []
