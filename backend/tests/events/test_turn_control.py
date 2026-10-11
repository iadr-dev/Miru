"""Cross-worker cancel/steer: a command sent from one worker reaches the worker running the turn."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from events.turn_control import (
    ControlMessage,
    ControlOp,
    NoopTurnControl,
    RedisTurnControl,
)


async def _first(control: RedisTurnControl, turn_id: str, thread_id: str) -> ControlMessage:
    async for message in control.listen(turn_id, thread_id):
        return message
    raise AssertionError("listener ended without a message")


async def test_a_announced_turn_can_be_found_by_turn_and_by_thread(fake_redis: Redis) -> None:
    control = RedisTurnControl(fake_redis)
    await control.announce(turn_id="t1", user_id="u1", thread_id="th1")

    by_turn = await control.find(turn_id="t1")
    by_thread = await control.find(thread_id="th1")

    assert by_turn is not None
    assert (by_turn.user_id, by_turn.thread_id) == ("u1", "th1")
    assert by_thread == by_turn


async def test_a_withdrawn_turn_is_gone_and_does_not_clear_a_newer_turn(fake_redis: Redis) -> None:
    control = RedisTurnControl(fake_redis)
    await control.announce(turn_id="old", user_id="u1", thread_id="th1")
    await control.announce(turn_id="new", user_id="u1", thread_id="th1")

    await control.withdraw(turn_id="old", thread_id="th1")

    assert await control.find(turn_id="old") is None
    newer = await control.find(thread_id="th1")
    assert newer is not None
    assert newer.turn_id == "new"


async def test_a_command_sent_by_another_worker_reaches_the_listener(fake_redis: Redis) -> None:
    owner = RedisTurnControl(fake_redis)  # the worker running the turn
    other = RedisTurnControl(fake_redis)  # the worker that received the HTTP request
    await owner.announce(turn_id="t1", user_id="u1", thread_id="th1")

    received = asyncio.create_task(_first(owner, "t1", "th1"))
    await asyncio.sleep(0.1)  # let the listener subscribe
    await other.send("t1", ControlMessage(op=ControlOp.STEER, text="focus on 2330"))

    message = await asyncio.wait_for(received, timeout=5)
    assert message == ControlMessage(op=ControlOp.STEER, text="focus on 2330")


async def test_cancel_is_delivered_too(fake_redis: Redis) -> None:
    control = RedisTurnControl(fake_redis)
    received = asyncio.create_task(_first(control, "t1", "th1"))
    await asyncio.sleep(0.1)

    await control.send("t1", ControlMessage(op=ControlOp.CANCEL))

    assert (await asyncio.wait_for(received, timeout=5)).op is ControlOp.CANCEL


async def test_the_single_process_default_is_disabled_and_finds_nothing() -> None:
    control = NoopTurnControl()

    await control.announce(turn_id="t1", user_id="u1", thread_id="th1")

    assert control.enabled is False
    assert await control.find(turn_id="t1") is None
    assert [m async for m in control.listen("t1", "th1")] == []
