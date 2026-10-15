"""Calls a request waits on use the bounded, timed-out Redis client; only the long-lived
subscriptions use the other. A Redis that stops answering must not hold a request (announcing a
turn, joining a quote stream, publishing) for the operating system's TCP timeout."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

from redis.asyncio import Redis

from config.settings import Settings
from events.bus import Event
from events.live_quote import RedisQuoteTransport
from events.live_quote_factory import build_data_update_broker, build_quote_feed
from events.turn_control import ControlMessage, ControlOp, RedisTurnControl
from netclient.redis_clients import request_path_redis, subscription_redis


class _Spy:
    """Records which calls reach it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def publish(self, channel: str, body: object) -> int:
        self.calls.append("publish")
        return 0

    async def set(self, *args: object, **kwargs: object) -> bool:
        self.calls.append("set")
        return True

    def pubsub(self) -> object:
        self.calls.append("pubsub")
        raise RuntimeError("stop after recording")


def _event() -> Event:
    return Event(kind="quote", seq=0, ts=datetime.now(UTC), data={})


def test_the_request_client_is_bounded_and_timed_out() -> None:
    settings = Settings(redis_request_timeout_seconds=2.0, redis_request_max_connections=7)

    pool = request_path_redis(settings).connection_pool

    assert pool.max_connections == 7  # type: ignore[attr-defined]
    assert pool.connection_kwargs["socket_timeout"] == 2.0  # type: ignore[attr-defined]


def test_the_subscription_client_has_no_reply_timeout() -> None:
    """An idle channel is not an error: a reply timeout would cut every quiet subscription."""
    kwargs = subscription_redis(Settings()).connection_pool.connection_kwargs  # type: ignore[attr-defined]

    assert kwargs.get("socket_timeout") is None
    assert kwargs["socket_connect_timeout"] == Settings().redis_request_timeout_seconds
    assert kwargs["health_check_interval"] == 30


async def test_quote_publishes_go_to_the_command_client() -> None:
    subscriptions, commands = _Spy(), _Spy()
    transport = RedisQuoteTransport(cast(Redis, subscriptions), commands=cast(Redis, commands))

    await transport.publish("quotes:2330", _event())

    assert commands.calls == ["publish"]
    assert subscriptions.calls == []


async def test_turn_control_commands_and_subscription_use_their_own_clients() -> None:
    subscriptions, commands = _Spy(), _Spy()
    control = RedisTurnControl(cast(Redis, subscriptions), commands=cast(Redis, commands))

    await control.announce(turn_id="t1", user_id="u1", thread_id="th1")
    await control.send("t1", ControlMessage(op=ControlOp.CANCEL))
    try:
        agen: AsyncIterator[ControlMessage] = control.listen("t1", "th1")
        await anext(agen)
    except RuntimeError:
        pass

    assert commands.calls == ["set", "set", "publish"]
    assert subscriptions.calls == ["pubsub"]


def test_the_builders_split_the_clients_when_none_is_injected() -> None:
    settings = Settings(event_bus_backend="redis")

    feed = build_quote_feed(settings, fetcher=object(), market_gate=object())  # type: ignore[arg-type]
    broker = build_data_update_broker(settings)

    assert feed is not None
    assert broker.publisher is not None
