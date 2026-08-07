"""`InMemoryEventBus` — publish/subscribe, envelope correctness, multi-subscriber fan-out."""

from __future__ import annotations

import asyncio

from events.bus import InMemoryEventBus


async def test_publish_returns_the_envelope_with_a_monotonic_seq() -> None:
    bus = InMemoryEventBus()
    first = bus.publish("turn.start", turn_id="t1", data={"thread_id": "th1"})
    second = bus.publish("turn.end", turn_id="t1", data={"status": "done"})

    assert first.seq == 0
    assert second.seq == 1
    assert first.turn_id == "t1"
    assert second.data == {"status": "done"}


async def test_seq_is_scoped_per_turn_not_global() -> None:
    bus = InMemoryEventBus()
    a1 = bus.publish("turn.start", turn_id="turn-a")
    b1 = bus.publish("turn.start", turn_id="turn-b")
    a2 = bus.publish("turn.end", turn_id="turn-a")

    assert a1.seq == 0
    assert b1.seq == 0
    assert a2.seq == 1


async def test_subscriber_receives_every_published_event_in_order() -> None:
    bus = InMemoryEventBus()
    received: list[str] = []

    async def consume() -> None:
        count = 0
        async for event in bus.subscribe():
            received.append(event.kind)
            count += 1
            if count == 2:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let the subscriber register before the first publish
    bus.publish("turn.start", turn_id="t1")
    bus.publish("turn.end", turn_id="t1")
    await task

    assert received == ["turn.start", "turn.end"]


async def test_subscribe_kinds_filter_only_matching_events() -> None:
    bus = InMemoryEventBus()
    received: list[str] = []

    async def consume() -> None:
        async for event in bus.subscribe(["usage"]):
            received.append(event.kind)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.publish("turn.start", turn_id="t1")  # filtered out
    bus.publish("usage", turn_id="t1")  # delivered
    await task

    assert received == ["usage"]


async def test_multiple_subscribers_each_get_every_matching_event() -> None:
    bus = InMemoryEventBus()
    received_a: list[str] = []
    received_b: list[str] = []

    async def consume(sink: list[str]) -> None:
        async for event in bus.subscribe():
            sink.append(event.kind)
            return

    task_a = asyncio.create_task(consume(received_a))
    task_b = asyncio.create_task(consume(received_b))
    await asyncio.sleep(0)
    bus.publish("activity", turn_id="t1")
    await asyncio.gather(task_a, task_b)

    assert received_a == ["activity"]
    assert received_b == ["activity"]
