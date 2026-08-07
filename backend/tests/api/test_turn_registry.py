"""Unit tests for the in-process turn registry — the shared handle a cross-request cancel/steer/
attach acts on. Verifies the thread index, cancel semantics (token flip + task cancel), and that
unregister clears both indexes."""

from __future__ import annotations

import asyncio

import pytest

from api.turn_registry import TurnRegistry
from conversation.turn_lifecycle import SimpleCancelToken
from events.bus import InMemoryEventBus


def _register(reg: TurnRegistry, *, turn_id: str, thread_id: str, user_id: str = "u1"):
    return reg.register(
        turn_id=turn_id,
        user_id=user_id,
        thread_id=thread_id,
        event_bus=InMemoryEventBus(),
        cancel_token=SimpleCancelToken(),
    )


def test_thread_index_resolves_active_turn() -> None:
    reg = TurnRegistry()
    _register(reg, turn_id="t1", thread_id="th1")
    assert reg.active_for_thread("th1").turn_id == "t1"  # type: ignore[union-attr]
    assert reg.get("t1") is not None
    assert reg.active_for_thread("missing") is None


def test_unregister_clears_both_indexes() -> None:
    reg = TurnRegistry()
    _register(reg, turn_id="t1", thread_id="th1")
    reg.unregister("t1")
    assert reg.get("t1") is None
    assert reg.active_for_thread("th1") is None


@pytest.mark.asyncio
async def test_request_cancel_flips_token_and_cancels_task() -> None:
    reg = TurnRegistry()
    running = _register(reg, turn_id="t1", thread_id="th1")

    started = asyncio.Event()

    async def _long_running() -> None:
        started.set()
        await asyncio.sleep(60)

    running.task = asyncio.create_task(_long_running())
    await started.wait()

    running.request_cancel()
    assert running.cancel_token.is_cancelled() is True
    with pytest.raises(asyncio.CancelledError):
        await running.task
