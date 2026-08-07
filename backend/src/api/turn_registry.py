"""In-process registry of in-flight turns, so a *separate* HTTP request can attach to, steer, or
cancel a turn that another request started.

`/agent/query` drives one turn inside a single request against a request-local `InMemoryEventBus`;
without a shared handle, a later `POST /threads/{id}/turns/{tid}/cancel` (a different request, no
shared memory) has nothing to act on. This registry is that shared handle: it maps a live
`turn_id` (and its thread) to the turn's event bus, its cooperative cancel token, and the
`asyncio.Task` running it.

The registry holds the turn's `EventBus` by its Protocol, not a concrete class, so a turn driven
against a Redis-backed bus (multi-worker) is tracked exactly like one on the in-process bus (dev):
a same-worker cancel/steer/attach still finds the live handle here. Cross-*worker* attach is not
this registry's job — that goes through the durable log + Redis channel reader; this remains the
same-worker coordination handle, unchanged in shape whether the bus is in-memory or Redis-backed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from conversation.turn_lifecycle import SimpleCancelToken
from events.bus import EventBus


@dataclass
class RunningTurn:
    """One live turn's shared coordination handle."""

    turn_id: str
    user_id: str
    thread_id: str
    event_bus: EventBus
    cancel_token: SimpleCancelToken
    task: asyncio.Task[None] | None = None
    steering: list[str] = field(default_factory=list[str])

    def request_cancel(self) -> None:
        """Flip the cooperative token *and* cancel the driving task. The token is the graceful
        signal (a cooperative loop stops at its next boundary); the task cancel is the hard stop
        for a driver that does not itself poll the token — the awaited work is interrupted."""
        self.cancel_token.cancel()
        if self.task is not None:
            self.task.cancel()

    def drain(self) -> tuple[str, ...]:
        """`agent.hooks.SteeringSource` — the running `AgentLoop`'s reach-in for the queue
        `POST .../steer` appends to. Returns everything queued since the last drain and clears
        it, so the same message is never folded into context twice across the Turn's remaining
        Steps."""
        drained = tuple(self.steering)
        self.steering.clear()
        return drained


class TurnRegistry:
    """Tracks live turns by `turn_id`, with a secondary `thread_id → turn_id` index so an
    attach/steer request that only knows the thread can find its active turn."""

    def __init__(self) -> None:
        self._by_turn: dict[str, RunningTurn] = {}
        self._active_by_thread: dict[str, str] = {}

    def register(
        self,
        *,
        turn_id: str,
        user_id: str,
        thread_id: str,
        event_bus: EventBus,
        cancel_token: SimpleCancelToken,
    ) -> RunningTurn:
        running = RunningTurn(
            turn_id=turn_id,
            user_id=user_id,
            thread_id=thread_id,
            event_bus=event_bus,
            cancel_token=cancel_token,
        )
        self._by_turn[turn_id] = running
        self._active_by_thread[thread_id] = turn_id
        return running

    def get(self, turn_id: str) -> RunningTurn | None:
        return self._by_turn.get(turn_id)

    def active_for_thread(self, thread_id: str) -> RunningTurn | None:
        turn_id = self._active_by_thread.get(thread_id)
        return self._by_turn.get(turn_id) if turn_id is not None else None

    def unregister(self, turn_id: str) -> None:
        running = self._by_turn.pop(turn_id, None)
        if running is not None and self._active_by_thread.get(running.thread_id) == turn_id:
            self._active_by_thread.pop(running.thread_id, None)


__all__ = ["RunningTurn", "TurnRegistry"]
