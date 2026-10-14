"""Config-selected wiring for the live-quote poller/fan-out and the `data.updated` broker, so
the interface layer never hardcodes a backend — the same seam-and-factory shape as
`events/factory.py`.

Backends follow `event_bus_backend`:

- **memory** — single-process: in-process demand refcount + in-process fan-out, and one shared
  in-process `data.updated` broker (publisher and reader are the same object).
- **redis** — multi-worker: Redis demand refcount + Redis pub/sub fan-out; a Redis `data.updated`
  publisher (leader side) and reader (API-worker side) over a shared channel.

The `QuoteFetcher` and `MarketOpenGate` are injected (built in the interface layer over
`dispatch_tool`/the calendar) so this module — like the poller itself — never imports `tools/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis

from config.settings import Settings
from events.bus import Event
from events.data_updates import (
    InMemoryDataUpdateBroker,
    RedisDataUpdatePublisher,
    RedisDataUpdateReader,
    SharedDataUpdateFeed,
)
from events.live_quote import (
    InMemoryDemandRegistry,
    InMemoryQuoteTransport,
    MarketOpenGate,
    QuoteFeed,
    QuoteFetcher,
    QuotePoller,
    QuoteTransport,
    RedisDemandRegistry,
    RedisQuoteTransport,
)
from ingest.data_updates import DataUpdatePublisher


@runtime_checkable
class DataUpdateReaderFactory(Protocol):
    """The API-worker side of the invalidation signal: opens a reader per `/updates/stream`
    connection (Redis subscribes its own pubsub; the in-memory backend hands back its shared
    bus subscription)."""

    def updates(self) -> AsyncIterator[Event]: ...


class _RedisDataUpdateReaderFactory:
    """Every `/updates/stream` connection in this process reads from ONE Redis subscription
    (`SharedDataUpdateFeed`), not one subscription, and one pooled connection, each."""

    def __init__(self, redis: Redis, *, channel: str) -> None:
        self._feed = SharedDataUpdateFeed(RedisDataUpdateReader(redis, channel=channel).updates)

    def updates(self) -> AsyncIterator[Event]:
        return self._feed.updates()


@dataclass(frozen=True)
class DataUpdateBroker:
    """Both sides of the invalidation signal, wired to the same backend. The leader publishes on
    `publisher`; each API-worker SSE connection reads from `reader_factory.updates()`. For the
    in-memory backend both sides are the one shared broker (a same-process publish reaches the
    reader); for Redis they share only the channel name, crossing processes over pub/sub."""

    publisher: DataUpdatePublisher
    reader_factory: DataUpdateReaderFactory


def build_data_update_broker(settings: Settings, redis: Redis | None = None) -> DataUpdateBroker:
    """Assemble publisher + reader-factory for the configured backend."""
    if settings.event_bus_backend == "redis":
        client: Redis = redis if redis is not None else Redis.from_url(settings.redis_url)
        return DataUpdateBroker(
            publisher=RedisDataUpdatePublisher(client, channel=settings.data_updated_key_prefix),
            reader_factory=_RedisDataUpdateReaderFactory(
                client, channel=settings.data_updated_key_prefix
            ),
        )
    broker = InMemoryDataUpdateBroker()
    return DataUpdateBroker(publisher=broker, reader_factory=broker)


def build_quote_feed(
    settings: Settings,
    *,
    fetcher: QuoteFetcher,
    market_gate: MarketOpenGate,
    redis: Redis | None = None,
) -> QuoteFeed:
    """Assemble the demand registry + transport + poller + feed for the configured backend."""
    cadence = {
        "normal": settings.quote_poll_cadence_normal_seconds,
        "saver": settings.quote_poll_cadence_saver_seconds,
    }
    transport: QuoteTransport
    if settings.event_bus_backend == "redis":
        client: Redis = redis if redis is not None else Redis.from_url(settings.redis_url)
        registry = RedisDemandRegistry(
            client,
            key_prefix=settings.quote_demand_key_prefix,
            max_symbols=settings.quote_stream_max_demanded_symbols,
        )
        transport = RedisQuoteTransport(client)
    else:
        registry = InMemoryDemandRegistry(  # type: ignore[assignment]  # both satisfy DemandRegistry
            max_symbols=settings.quote_stream_max_demanded_symbols
        )
        transport = InMemoryQuoteTransport()
    poller = QuotePoller(
        registry=registry,
        transport=transport,
        fetcher=fetcher,
        market_gate=market_gate,
        channel_prefix=settings.quote_channel_key_prefix,
        cadence_seconds=cadence,
    )
    return QuoteFeed(
        poller=poller,
        transport=transport,
        channel_prefix=settings.quote_channel_key_prefix,
        max_symbols_per_connection=settings.quote_stream_max_symbols_per_connection,
    )


__all__ = [
    "DataUpdateBroker",
    "DataUpdateReaderFactory",
    "build_data_update_broker",
    "build_quote_feed",
]
