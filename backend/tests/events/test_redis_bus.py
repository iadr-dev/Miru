"""`RedisEventBus` + `RedisStreamReader` against fakeredis (no live Redis in the default
profile). Covers: local same-worker fan-out, the durable-log + Redis-channel mirror, and the
cross-worker reader following live off the channel.

The full router-level replay-then-live resume (the gapless/duplicate-free boundary) is tested at
the HTTP seam in tests/api; here we prove the transport primitives each mirror correctly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import ThreadRow, TurnRow
from events.redis_bus import RedisEventBus, RedisStreamReader, channel_for
from events.stream_store import SqlAlchemyStreamEventStore


async def _seed_turn(factory: async_sessionmaker, *, user_id: str = "u1") -> str:
    now = datetime.now(UTC)
    thread_id, turn_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as session:
        session.add(
            ThreadRow(
                id=thread_id, user_id=user_id, last_message_at=now, created_at=now, updated_at=now
            )
        )
        await session.flush()
        session.add(
            TurnRow(
                id=turn_id,
                thread_id=thread_id,
                user_id=user_id,
                role="assistant",
                status="running",
                created_at=now,
            )
        )
        await session.commit()
    return turn_id


async def test_local_subscribe_receives_published_events(fake_redis: Redis) -> None:
    """The producing worker's own stream is the in-process fan-out — race-free, no Pub/Sub
    cold-start gap."""
    bus = RedisEventBus(fake_redis, turn_id="t1", user_id="u1")
    received: list[str] = []

    async def consume() -> None:
        async for event in bus.subscribe():
            received.append(event.kind)
            if event.kind == "turn.end":
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.publish("turn.start", turn_id="t1")
    bus.publish("turn.end", turn_id="t1")
    await asyncio.wait_for(task, timeout=2)

    assert received == ["turn.start", "turn.end"]
    await bus.aclose()


async def test_publish_mirrors_to_durable_log(
    fake_redis: Redis, session_factory: async_sessionmaker
) -> None:
    turn_id = await _seed_turn(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)
    bus = RedisEventBus(fake_redis, turn_id=turn_id, user_id="u1", stream_store=store)

    bus.publish("turn.start", turn_id=turn_id)
    bus.publish("message", turn_id=turn_id)
    bus.publish("turn.end", turn_id=turn_id)
    await bus.aclose()  # drains the background mirror-writer

    replayed = await store.replay_since(turn_id, after_seq=-1)
    assert [e.kind for e in replayed] == ["turn.start", "message", "turn.end"]


async def test_cross_worker_reader_follows_live_channel(fake_redis: Redis) -> None:
    """A reader on any worker subscribes to the turn's channel and receives events the producing
    bus publishes to Redis — the cross-process live path."""
    turn_id = "t-cross"
    reader = RedisStreamReader(fake_redis)
    received: list[int] = []
    subscribed = asyncio.Event()

    async def consume() -> None:
        agen = reader.live(turn_id)
        # Drive one iteration setup so the channel subscription is active before we publish.
        subscribed.set()
        async for event in agen:
            received.append(event.seq)
            if event.kind == "turn.end":
                return

    task = asyncio.create_task(consume())
    await subscribed.wait()
    await asyncio.sleep(0.05)  # let pubsub.subscribe() land before publishing

    bus = RedisEventBus(fake_redis, turn_id=turn_id, user_id="u1")
    bus.publish("message", turn_id=turn_id)
    bus.publish("turn.end", turn_id=turn_id)
    await bus.aclose()

    await asyncio.wait_for(task, timeout=2)
    assert received == [0, 1]


def test_channel_key_is_turn_scoped() -> None:
    assert channel_for("abc") == "stream:abc"
