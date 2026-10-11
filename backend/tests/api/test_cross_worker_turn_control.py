"""Cancel/steer reach a turn that another worker is running (via `TurnControl`), and are refused
for someone else's turn - the single-process path is covered by the existing registry tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from redis.asyncio import Redis

from accounts.models import User
from api.dependencies import AppState
from api.errors import ApiError
from api.routers.threads_actions import SteerRequest, cancel_turn, steer_turn
from api.turn_registry import RunningTurn, TurnRegistry
from conversation.store import ConversationStore
from conversation.turn_lifecycle import SimpleCancelToken
from events.bus import InMemoryEventBus
from events.kinds import EventKind
from events.turn_control import ControlMessage, ControlOp, RedisTurnControl
from tests.events.conftest import fake_redis  # noqa: F401 - fixture


def _user(user_id: str) -> User:
    now = datetime.now(UTC)
    return User(id=user_id, created_at=now, updated_at=now)


class _Store:
    async def get_thread(self, *, user_id: str, thread_id: str) -> object:
        return SimpleNamespace(id=thread_id, user_id=user_id)


def _state(control: RedisTurnControl) -> AppState:
    # The two routes only read these two collaborators.
    return cast(AppState, SimpleNamespace(turn_registry=TurnRegistry(), turn_control=control))


async def _collect(control: RedisTurnControl, count: int) -> list[ControlMessage]:
    received: list[ControlMessage] = []
    async for message in control.listen("t1", "th1"):
        received.append(message)
        if len(received) == count:
            break
    return received


async def test_cancel_on_a_worker_that_does_not_run_the_turn_is_relayed(
    fake_redis: Redis,  # noqa: F811 - the imported fixture
) -> None:
    control = RedisTurnControl(fake_redis)
    await control.announce(turn_id="t1", user_id="u1", thread_id="th1")
    owner = asyncio.create_task(_collect(control, 1))
    await asyncio.sleep(0.1)

    result = await cancel_turn(
        "th1", "t1", _user("u1"), _state(control), cast(ConversationStore, _Store())
    )

    assert result == {"status": "cancelling"}
    assert (await asyncio.wait_for(owner, 5))[0].op is ControlOp.CANCEL


async def test_steer_on_another_worker_is_relayed_with_its_text(
    fake_redis: Redis,  # noqa: F811
) -> None:
    control = RedisTurnControl(fake_redis)
    await control.announce(turn_id="t1", user_id="u1", thread_id="th1")
    owner = asyncio.create_task(_collect(control, 1))
    await asyncio.sleep(0.1)

    result = await steer_turn(
        "th1",
        SteerRequest(message="look at 2330"),
        _user("u1"),
        _state(control),
        cast(ConversationStore, _Store()),
    )

    assert result == {"status": "accepted"}
    assert (await asyncio.wait_for(owner, 5))[0] == ControlMessage(
        op=ControlOp.STEER, text="look at 2330"
    )


async def test_another_users_turn_is_not_found_and_nothing_is_sent(
    fake_redis: Redis,  # noqa: F811
) -> None:
    control = RedisTurnControl(fake_redis)
    await control.announce(turn_id="t1", user_id="owner", thread_id="th1")

    with pytest.raises(ApiError) as cancelled:
        await cancel_turn(
            "th1", "t1", _user("intruder"), _state(control), cast(ConversationStore, _Store())
        )
    with pytest.raises(ApiError) as steered:
        await steer_turn(
            "th1",
            SteerRequest(message="x"),
            _user("intruder"),
            _state(control),
            cast(ConversationStore, _Store()),
        )

    assert cancelled.value.status_code == 404
    assert steered.value.status_code == 409


async def test_a_relayed_command_acts_on_the_running_turn() -> None:
    bus = InMemoryEventBus()
    running = RunningTurn(
        turn_id="t1",
        user_id="u1",
        thread_id="th1",
        event_bus=bus,
        cancel_token=SimpleCancelToken(),
    )
    first_event = asyncio.create_task(anext(aiter(bus.subscribe())))
    await asyncio.sleep(0)  # let the subscriber register

    running.apply(ControlMessage(op=ControlOp.STEER, text="focus"))
    running.apply(ControlMessage(op=ControlOp.CANCEL))

    assert running.drain() == ("focus",)
    assert running.cancel_token.is_cancelled()
    event = await asyncio.wait_for(first_event, 5)
    assert event.kind == EventKind.STEERING_ACCEPTED
