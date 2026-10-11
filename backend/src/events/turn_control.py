"""Cross-worker cancel/steer for in-flight turns.

`TurnRegistry` is process-local: with several API workers, a `cancel` or `steer` request can land
on a worker that is not running the turn and would find nothing. This seam closes that gap. The
worker running a turn *announces* it (who owns it, which thread) and *listens* on a per-turn
control channel; any worker can then look the turn up and *send* it a command.

Two backends behind one Protocol, selected by the same `event_bus_backend` setting as the event
transport: **memory** (single process - the registry already sees every turn, so this is a no-op
that reports itself disabled) and **redis** (announce keys + Pub/Sub channel). Like the event bus,
this is infrastructure plumbing for the interaction layer's own lifecycle, not source-data I/O.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

import structlog
from redis.asyncio import Redis

from config.settings import Settings

logger = structlog.get_logger(__name__)

_RUNNING_PREFIX = "turn:running:"
_THREAD_PREFIX = "turn:thread:"
_CHANNEL_PREFIX = "turn-ctl:"
# An announcement outlives a crashed worker by at most this long; the owner refreshes it while it
# listens, so a live turn never expires.
ANNOUNCE_TTL_SECONDS = 120
_REFRESH_EVERY_SECONDS = 30


class ControlOp(StrEnum):
    CANCEL = "cancel"
    STEER = "steer"


@dataclass(frozen=True)
class ControlMessage:
    op: ControlOp
    text: str = ""


@dataclass(frozen=True)
class RemoteTurn:
    turn_id: str
    user_id: str
    thread_id: str


@runtime_checkable
class TurnControl(Protocol):
    enabled: bool

    async def announce(self, *, turn_id: str, user_id: str, thread_id: str) -> None: ...

    async def withdraw(self, *, turn_id: str, thread_id: str) -> None: ...

    async def find(
        self, *, turn_id: str | None = None, thread_id: str | None = None
    ) -> RemoteTurn | None: ...

    async def send(self, turn_id: str, message: ControlMessage) -> None: ...

    def listen(self, turn_id: str, thread_id: str) -> AsyncIterator[ControlMessage]: ...


class NoopTurnControl:
    """Single-process default: every turn is in this process's registry, so nothing is shared."""

    enabled = False

    async def announce(self, *, turn_id: str, user_id: str, thread_id: str) -> None:
        del turn_id, user_id, thread_id

    async def withdraw(self, *, turn_id: str, thread_id: str) -> None:
        del turn_id, thread_id

    async def find(
        self, *, turn_id: str | None = None, thread_id: str | None = None
    ) -> RemoteTurn | None:
        del turn_id, thread_id
        return None

    async def send(self, turn_id: str, message: ControlMessage) -> None:
        del turn_id, message

    async def listen(self, turn_id: str, thread_id: str) -> AsyncIterator[ControlMessage]:
        del turn_id, thread_id
        for message in tuple[ControlMessage, ...]():  # nothing to listen to; still a generator
            yield message


class RedisTurnControl:
    enabled = True

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def announce(self, *, turn_id: str, user_id: str, thread_id: str) -> None:
        payload = json.dumps({"user_id": user_id, "thread_id": thread_id})
        await self._redis.set(f"{_RUNNING_PREFIX}{turn_id}", payload, ex=ANNOUNCE_TTL_SECONDS)
        await self._redis.set(f"{_THREAD_PREFIX}{thread_id}", turn_id, ex=ANNOUNCE_TTL_SECONDS)

    async def withdraw(self, *, turn_id: str, thread_id: str) -> None:
        await self._redis.delete(f"{_RUNNING_PREFIX}{turn_id}")
        # Only clear the thread pointer if it still names this turn (a newer turn may own it).
        current = await self._redis.get(f"{_THREAD_PREFIX}{thread_id}")
        if current is not None and _text(current) == turn_id:
            await self._redis.delete(f"{_THREAD_PREFIX}{thread_id}")

    async def find(
        self, *, turn_id: str | None = None, thread_id: str | None = None
    ) -> RemoteTurn | None:
        if turn_id is None and thread_id is not None:
            pointed = await self._redis.get(f"{_THREAD_PREFIX}{thread_id}")
            turn_id = _text(pointed) if pointed is not None else None
        if turn_id is None:
            return None
        raw = await self._redis.get(f"{_RUNNING_PREFIX}{turn_id}")
        if raw is None:
            return None
        data = cast(dict[str, str], json.loads(_text(raw)))
        return RemoteTurn(turn_id=turn_id, user_id=data["user_id"], thread_id=data["thread_id"])

    async def send(self, turn_id: str, message: ControlMessage) -> None:
        body = json.dumps({"op": message.op.value, "text": message.text}, ensure_ascii=False)
        await self._redis.publish(f"{_CHANNEL_PREFIX}{turn_id}", body)

    async def listen(self, turn_id: str, thread_id: str) -> AsyncIterator[ControlMessage]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"{_CHANNEL_PREFIX}{turn_id}")
        try:
            while True:
                raw = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_REFRESH_EVERY_SECONDS
                )
                # Keep the announcement alive for as long as this turn is being listened to.
                await self._redis.expire(f"{_RUNNING_PREFIX}{turn_id}", ANNOUNCE_TTL_SECONDS)
                await self._redis.expire(f"{_THREAD_PREFIX}{thread_id}", ANNOUNCE_TTL_SECONDS)
                if raw is None:
                    continue
                try:
                    data = cast(dict[str, str], json.loads(_text(raw["data"])))
                    yield ControlMessage(op=ControlOp(data["op"]), text=data.get("text", ""))
                except (ValueError, KeyError):
                    logger.warning("turn_control_bad_message", turn_id=turn_id)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]


def _text(value: bytes | str) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def build_turn_control(settings: Settings) -> TurnControl:
    """Select the backend from config, mirroring `events.factory.build_event_bus_factory`."""
    if settings.event_bus_backend == "redis":
        return RedisTurnControl(Redis.from_url(settings.redis_url))
    return NoopTurnControl()


__all__ = [
    "ANNOUNCE_TTL_SECONDS",
    "ControlMessage",
    "ControlOp",
    "NoopTurnControl",
    "RedisTurnControl",
    "RemoteTurn",
    "TurnControl",
    "build_turn_control",
]
