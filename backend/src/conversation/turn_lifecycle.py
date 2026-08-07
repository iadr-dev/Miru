"""The turn-lifecycle state machine: `accepted -> running -> (cancelled | done | error)`,
idempotency-key handling, and the integration point into `agent.loop.AgentLoop.run_turn`.

This module owns *state transitions and persistence*, not the HTTP/SSE transport — no FastAPI
route lives here (that is a Phase-11 concern per this phase's brief). `TurnLifecycle.start`
is what a future `POST /agent/query` handler calls; it returns once the turn has fully run (or
been cancelled/erred), leaving actual streaming to whatever subscribes to the event bus
concurrently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from agent.loop import AgentLoop, TurnCancelledError
from conversation.models import Framework, MessageBlockType, TextBlock, TurnRole
from conversation.store import ConversationStore
from events.bus import EventBus
from tools.base import ToolContext

logger = structlog.get_logger(__name__)


class TurnLifecycleState(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


_VALID_TRANSITIONS: dict[TurnLifecycleState, frozenset[TurnLifecycleState]] = {
    TurnLifecycleState.ACCEPTED: frozenset({TurnLifecycleState.RUNNING}),
    TurnLifecycleState.RUNNING: frozenset(
        {TurnLifecycleState.DONE, TurnLifecycleState.CANCELLED, TurnLifecycleState.ERROR}
    ),
    TurnLifecycleState.DONE: frozenset(),
    TurnLifecycleState.CANCELLED: frozenset(),
    TurnLifecycleState.ERROR: frozenset(),
}


class InvalidTurnTransitionError(RuntimeError):
    pass


@dataclass
class SimpleCancelToken:
    """The default `agent.hooks.CancelToken` implementation: a flag `cancel()` sets and the
    loop polls between Steps."""

    _cancelled: bool = field(default=False, init=False)

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


@dataclass
class TurnHandle:
    """What `TurnLifecycle.start` hands back to a caller that wants to cancel an in-flight
    turn — deliberately not the full `TurnResult`, so a cancel request doesn't need to await
    the turn's completion."""

    turn_id: str
    cancel_token: SimpleCancelToken

    def cancel(self) -> None:
        self.cancel_token.cancel()


class TurnLifecycle:
    """Drives one turn through `accepted -> running -> (done|cancelled|error)`, persisting
    the transitions via `ConversationStore` and publishing `turn.start`/`turn.end` on the
    event bus. Idempotency: a repeated `start()` call with the same `idempotency_key` for the
    same thread returns the same `TurnHandle` rather than spawning a second turn — enforced by
    an in-process de-dupe map here; the store's own `UNIQUE(user_id, idempotency_key)`
    constraint is the durable backstop for a multi-process deployment."""

    def __init__(self, *, conversation_store: ConversationStore, event_bus: EventBus) -> None:
        self._conversation_store = conversation_store
        self._event_bus = event_bus
        self._idempotency_seen: dict[tuple[str, str], str] = {}
        self._states: dict[str, TurnLifecycleState] = {}

    def state_of(self, turn_id: str) -> TurnLifecycleState:
        return self._states.get(turn_id, TurnLifecycleState.ACCEPTED)

    def _transition(self, turn_id: str, new_state: TurnLifecycleState) -> None:
        current = self.state_of(turn_id)
        if new_state not in _VALID_TRANSITIONS[current]:
            raise InvalidTurnTransitionError(f"{current} -> {new_state} is not a valid transition")
        self._states[turn_id] = new_state

    async def start(  # noqa: PLR0913 — one field per turn-start dimension
        self,
        *,
        loop: AgentLoop,
        user_id: str,
        thread_id: str,
        user_message: str,
        tool_context: ToolContext,
        idempotency_key: str | None = None,
        cancel_token: SimpleCancelToken | None = None,
    ) -> TurnHandle:
        """Run one turn to completion (or cancellation/error) and persist it. Returns a
        `TurnHandle` — by the time this coroutine returns, the handle's `cancel()` is already
        moot (the turn has finished), but the handle is still useful for its `turn_id`.
        `cancel_token` is normally omitted (a fresh one is created per turn); a caller may
        pass one pre-flipped to exercise the cancelled path deterministically."""
        if idempotency_key is not None:
            dedupe_key = (thread_id, idempotency_key)
            existing_turn_id = self._idempotency_seen.get(dedupe_key)
            if existing_turn_id is not None:
                return TurnHandle(turn_id=existing_turn_id, cancel_token=SimpleCancelToken())

        turn_id = str(uuid.uuid4())
        cancel_token = cancel_token or SimpleCancelToken()
        if idempotency_key is not None:
            self._idempotency_seen[(thread_id, idempotency_key)] = turn_id

        self._states[turn_id] = TurnLifecycleState.ACCEPTED
        self._event_bus.publish("turn.start", turn_id=turn_id, data={"thread_id": thread_id})
        self._transition(turn_id, TurnLifecycleState.RUNNING)

        loop.cancel_token = cancel_token

        try:
            result = await loop.run_turn(user_message, tool_context=tool_context)
        except TurnCancelledError:
            self._transition(turn_id, TurnLifecycleState.CANCELLED)
            await self._conversation_store.append_turn(
                user_id=user_id,
                thread_id=thread_id,
                role=TurnRole.ASSISTANT,
                blocks=[(MessageBlockType.TEXT, TextBlock(text="[turn cancelled]"))],
                parent_message_id=await self._current_leaf(thread_id),
                framework=Framework.SINGLE,
                idempotency_key=idempotency_key,
            )
            self._event_bus.publish("turn.end", turn_id=turn_id, data={"status": "cancelled"})
            return TurnHandle(turn_id=turn_id, cancel_token=cancel_token)
        except Exception as exc:
            self._transition(turn_id, TurnLifecycleState.ERROR)
            self._event_bus.publish(
                "turn.end", turn_id=turn_id, data={"status": "error", "message": str(exc)}
            )
            logger.error("turn_errored", turn_id=turn_id, error=str(exc))
            raise

        self._transition(turn_id, TurnLifecycleState.DONE)
        await self._conversation_store.append_turn(
            user_id=user_id,
            thread_id=thread_id,
            role=TurnRole.ASSISTANT,
            blocks=[(MessageBlockType.TEXT, TextBlock(text=result.final_text))],
            parent_message_id=await self._current_leaf(thread_id),
            framework=Framework.SINGLE,
            idempotency_key=idempotency_key,
        )
        self._event_bus.publish("turn.end", turn_id=turn_id, data={"status": "done"})
        return TurnHandle(turn_id=turn_id, cancel_token=cancel_token)

    async def _current_leaf(self, thread_id: str) -> str | None:
        path = await self._conversation_store.active_path(thread_id=thread_id)
        return path[-1].id if path else None


__all__ = [
    "InvalidTurnTransitionError",
    "SimpleCancelToken",
    "TurnHandle",
    "TurnLifecycle",
    "TurnLifecycleState",
]
