"""The shared, demand-driven live-quote poller + its SSE fan-out.

Why this belongs in the interaction layer beside the event transport, not `sources/`: the same
reasoning as `redis_bus.py`. That "only `sources/` performs network I/O" rule governs
market-data *acquisition* — a caller names a capability + `(symbol, market)` and the router
resolves a source, so governance/entitlement can never be bypassed on fetched data. This module
opens no data source and fetches no market data itself: the actual quote fetch is an injected
`QuoteFetcher` seam that the interface layer builds over `dispatch_tool` + `get_price_quote`,
exactly the path the one-shot `/quote` uses. What lives here is the *poller and fan-out* around
that unchanged fetch — a refcounted demand set plus a Redis (or in-process) channel that turns
one upstream snapshot into N client pushes. Redis here is transport plumbing, the same category
as the event bus's own cross-worker channel.

Concurrency model — stated explicitly. This is a **per-worker** poller: each API-worker process
runs one poll loop that only touches symbols with a subscriber *on this worker*. The demand
refcount in Redis is the global union (so the demanded-symbol cap is enforced across the whole
fleet and the set is observable), but the poll loop iterates the worker's own local subscribers.
The alternative — a single leader-elected poller process — would fetch each symbol exactly once
fleet-wide, but it introduces a second leader-election domain (distinct from ingest's) for a
comparatively small saving: with the per-worker model, a symbol watched on two workers costs at
most one extra snapshot *per worker that has a subscriber*, never one per client. Since the whole
point is collapsing N clients → 1 fetch, and clients for one symbol tend to land on few workers,
the per-worker model captures nearly all of the saving without a new election concept. The
duplicate-poll hazard the concurrency contract names is still bounded: the demand set stays a
refcount, and no worker ever polls a symbol nobody on it is watching.

Cadence tie-break: a symbol is polled at the **fastest** interval any of its current subscribers
requested. If one client asks for `normal` (~5s) and another for `saver` (~20-30s) on the same
symbol, the poll runs at `normal`'s faster rate so the `normal` subscriber is not starved — the
`saver` subscriber simply receives more frames than it minimally needed, never fewer, and one
poll still fans out to both. `saver` widens the interval only when *every* live subscriber to
that symbol asked for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast, runtime_checkable

import structlog
from redis.asyncio import Redis

from events.bus import Event
from events.pubsub import close_pubsub
from schemas.enums import Market

logger = structlog.get_logger(__name__)

Cadence = str  # "normal" | "saver" — the two client hint tiers


@dataclass(frozen=True)
class QuoteFetchResult:
    """One snapshot the poller fans out: the dispatched quote payload plus its own source
    timestamp (`as_of`). `as_of` is the snapshot's currency, not the publish time — the client's
    monotonic discard validator drops any tick not newer than what it already shows."""

    quote: dict[str, object]
    as_of: str | None


@runtime_checkable
class QuoteFetcher(Protocol):
    """The unchanged fetch path, injected so this layer never imports `tools/`. The interface
    layer builds a concrete fetcher that calls `dispatch_tool` over `get_price_quote` exactly as
    the one-shot `/quote` does — the entitlement gate is applied there, not rebuilt here."""

    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult: ...


@runtime_checkable
class MarketOpenGate(Protocol):
    """Answers "is `market`'s session open at `now`" — the signal that keeps the poller to
    market hours (bounded observation, never an off-hours scan)."""

    def is_open(self, market: Market, now: datetime) -> bool: ...


@runtime_checkable
class DemandRegistry(Protocol):
    """The cross-process demand set: a per-`(market, symbol)` refcount plus the global
    demanded-symbol cap. `subscribe` returns `False` (without incrementing) when admitting the
    symbol would exceed the global cap, so an over-cap request degrades that one symbol rather
    than crashing the connection."""

    async def subscribe(self, market: Market, symbol: str) -> bool: ...

    async def unsubscribe(self, market: Market, symbol: str) -> None: ...


@runtime_checkable
class QuoteTransport(Protocol):
    """The fan-out channel: publish one snapshot to `quote:{market}:{symbol}`, subscribe a
    connection to the channels for the symbols it watches."""

    async def publish(self, channel: str, event: Event) -> None: ...

    def subscribe(self, channels: list[str]) -> AsyncIterator[Event]: ...


def channel_for(market: Market, symbol: str, *, prefix: str) -> str:
    return f"{prefix}:{market.value}:{symbol}"


def _encode(event: Event) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False)


def _decode(raw: bytes | str) -> Event:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return Event.model_validate(json.loads(text))


# ---- demand registry backends --------------------------------------------------------------


class RedisDemandRegistry:
    """Redis-backed refcount per `(market, symbol)`. Increment/decrement are atomic `HINCRBY`s
    (the same no-read-then-write discipline as the rate-limit/quota counters), so two workers
    subscribing the same symbol concurrently can never lose an update. The global cap is checked
    reserve-before-act: increment first, and if that increment *created* a new symbol field past
    the cap, undo it and reject — never a check-then-increment race that admits over the ceiling.
    """

    def __init__(self, redis: Redis, *, key_prefix: str, max_symbols: int) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._max_symbols = max_symbols

    def _key(self, market: Market) -> str:
        return f"{self._prefix}:{market.value}"

    async def subscribe(self, market: Market, symbol: str) -> bool:
        key = self._key(market)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hincrby(key, symbol, 1)
            pipe.hlen(key)
            results = await pipe.execute()
        new_count = int(results[0])
        distinct = int(results[1])
        if new_count == 1 and distinct > self._max_symbols:
            # This subscribe created a brand-new demanded symbol beyond the global cap — undo it
            # so the ceiling holds, and reject just this symbol.
            await self._redis.hdel(key, symbol)
            logger.info("quote_demand_cap_reached", market=market.value, cap=self._max_symbols)
            return False
        return True

    async def unsubscribe(self, market: Market, symbol: str) -> None:
        key = self._key(market)
        remaining = await self._redis.hincrby(key, symbol, -1)
        if int(remaining) <= 0:
            await self._redis.hdel(key, symbol)


class InMemoryDemandRegistry:
    """Single-process demand refcount — correct where one worker holds (dev, tests). Not atomic
    across processes; the Redis backend is what the multi-worker deployment uses."""

    def __init__(self, *, max_symbols: int) -> None:
        self._max_symbols = max_symbols
        self._counts: dict[tuple[str, str], int] = {}

    async def subscribe(self, market: Market, symbol: str) -> bool:
        key = (market.value, symbol)
        if key not in self._counts and len(self._counts) >= self._max_symbols:
            logger.info("quote_demand_cap_reached", market=market.value, cap=self._max_symbols)
            return False
        self._counts[key] = self._counts.get(key, 0) + 1
        return True

    async def unsubscribe(self, market: Market, symbol: str) -> None:
        key = (market.value, symbol)
        remaining = self._counts.get(key, 0) - 1
        if remaining <= 0:
            self._counts.pop(key, None)
        else:
            self._counts[key] = remaining


# ---- transport backends --------------------------------------------------------------------


class RedisQuoteTransport:
    """Fan-out over Redis Pub/Sub — the multi-worker path. One publish reaches every worker that
    has a subscriber on that symbol's channel; each worker relays to its own SSE connections."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, event: Event) -> None:
        await self._redis.publish(channel, _encode(event))

    async def subscribe(self, channels: list[str]) -> AsyncIterator[Event]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(*channels)
        try:
            messages = cast("AsyncIterator[dict[str, object]]", pubsub.listen())
            async for message in messages:
                if message.get("type") != "message":
                    continue
                data = message["data"]
                if isinstance(data, bytes | str):
                    yield _decode(data)
        finally:
            await pubsub.unsubscribe(*channels)
            await close_pubsub(pubsub)


class InMemoryQuoteTransport:
    """Single-process fan-out keyed by channel name — the default backend. One `asyncio.Queue`
    per subscription; publish delivers the intact `Event` (preserving `kind`/`seq`) to every
    subscription that asked for the channel. A cancelled subscriber removes itself on exit."""

    def __init__(self) -> None:
        self._subscribers: dict[int, tuple[frozenset[str], asyncio.Queue[Event]]] = {}
        self._next_id = itertools.count()

    async def publish(self, channel: str, event: Event) -> None:
        for channels, queue in self._subscribers.values():
            if channel in channels:
                queue.put_nowait(event)

    async def subscribe(self, channels: list[str]) -> AsyncIterator[Event]:
        subscriber_id = next(self._next_id)
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[subscriber_id] = (frozenset(channels), queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.pop(subscriber_id, None)


# ---- the poller ----------------------------------------------------------------------------


@dataclass
class _LocalSymbol:
    """Per-worker demand for one symbol: how many local subscribers want it at each cadence, and
    when it was last polled (monotonic seconds)."""

    normal: int = 0
    saver: int = 0
    last_polled: float | None = None

    @property
    def live(self) -> bool:
        return self.normal > 0 or self.saver > 0


class QuotePoller:
    """One poll loop per worker. Ticks on a fine granularity; for each locally-demanded symbol
    that is *due* (by its fastest requested cadence) and whose market is open, it fetches exactly
    one snapshot via the injected `QuoteFetcher` and publishes it to the symbol's channel. The
    loop runs only while this worker has ≥1 local subscriber — it starts on the first subscribe
    and stops when the last one disconnects, so an idle worker touches nothing."""

    def __init__(  # noqa: PLR0913 — one injected collaborator/config per parameter, all keyword-only
        self,
        *,
        registry: DemandRegistry,
        transport: QuoteTransport,
        fetcher: QuoteFetcher,
        market_gate: MarketOpenGate,
        channel_prefix: str,
        cadence_seconds: dict[Cadence, float],
        auto_poll: bool = True,
    ) -> None:
        self._registry = registry
        self._transport = transport
        self._fetcher = fetcher
        self._gate = market_gate
        self._channel_prefix = channel_prefix
        self._cadence_seconds = cadence_seconds
        # `auto_poll` is the production default: the background loop starts on first subscribe.
        # A unit test that drives `poll_tick` by hand sets it False so the loop's own timed ticks
        # don't interleave with (and perturb the due-window of) its manual ones.
        self._auto_poll = auto_poll
        self._local: dict[tuple[Market, str], _LocalSymbol] = {}
        self._seq = itertools.count()
        self._loop_task: asyncio.Task[None] | None = None

    def _interval(self, entry: _LocalSymbol) -> float:
        # Fastest requested cadence wins so a `normal` subscriber is never starved by a
        # co-subscribed `saver`.
        if entry.normal > 0:
            return self._cadence_seconds["normal"]
        return self._cadence_seconds["saver"]

    def _granularity(self) -> float:
        return min(self._cadence_seconds.values())

    async def add_subscriber(self, market: Market, symbol: str, cadence: Cadence) -> bool:
        """Register one local subscriber. Returns `False` (nothing registered) if the global
        demanded-symbol cap rejects a new symbol — the caller drops that symbol gracefully."""
        entry = self._local.get((market, symbol))
        if entry is None:
            if not await self._registry.subscribe(market, symbol):
                return False
            entry = _LocalSymbol()
            self._local[(market, symbol)] = entry
        else:
            # Already local — still take a demand refcount so the global count reflects every
            # subscriber, but it can never be rejected (the symbol is already under the cap).
            await self._registry.subscribe(market, symbol)
        if cadence == "saver":
            entry.saver += 1
        else:
            entry.normal += 1
        self._ensure_loop()
        return True

    async def remove_subscriber(self, market: Market, symbol: str, cadence: Cadence) -> None:
        await self._registry.unsubscribe(market, symbol)
        entry = self._local.get((market, symbol))
        if entry is None:
            return
        if cadence == "saver":
            entry.saver = max(0, entry.saver - 1)
        else:
            entry.normal = max(0, entry.normal - 1)
        if not entry.live:
            self._local.pop((market, symbol), None)

    def _ensure_loop(self) -> None:
        if not self._auto_poll:
            return
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        # Sleep before the first poll (not after): one cadence interval of warm-up lets any
        # co-subscribers to a symbol register before the first upstream spend, so the very first
        # fetch already fans out to everyone watching — strictly better for the Sponsor quota than
        # spending on tick zero for one subscriber and re-spending as others arrive.
        try:
            while self._local:
                await asyncio.sleep(self._granularity())
                await self.poll_tick()
        finally:
            self._loop_task = None

    async def poll_tick(self, *, now: datetime | None = None, clock: float | None = None) -> int:
        """One poll pass: fetch + publish every due, market-open, locally-demanded symbol once.
        Returns how many snapshots it fetched. `now` sets the market-open check + frame `ts`;
        `clock` overrides the monotonic due-window reference (tests inject it to drive cadence
        deterministically instead of waiting on the wall/loop timer)."""
        moment = now or datetime.now(UTC)
        tick_clock = clock if clock is not None else asyncio.get_event_loop().time()
        fetched = 0
        for (market, symbol), entry in list(self._local.items()):
            if not entry.live:
                continue
            interval = self._interval(entry)
            if entry.last_polled is not None and tick_clock - entry.last_polled < interval:
                continue
            market_open = self._gate.is_open(market, moment)
            entry.last_polled = tick_clock
            if not market_open:
                continue  # off-hours: the poller idles this symbol until the session opens
            try:
                await self._poll_one(market, symbol, moment, market_open)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One symbol's fetch or publish failing must not end the loop: it would stop the
                # stream for every viewer on this worker until someone else subscribed.
                logger.warning(
                    "quote_poll_failed", symbol=symbol, market=market.value, exc_info=True
                )
                continue
            fetched += 1
        return fetched

    async def _poll_one(
        self, market: Market, symbol: str, moment: datetime, market_open: bool
    ) -> None:
        result = await self._fetcher.fetch(symbol=symbol, market=market)
        event = Event(
            kind="quote",
            seq=next(self._seq),
            ts=moment,
            data={
                "symbol": symbol,
                "market": market.value,
                "quote": result.quote,
                "as_of": result.as_of,
                "market_open": market_open,
            },
        )
        await self._transport.publish(
            channel_for(market, symbol, prefix=self._channel_prefix), event
        )

    async def aclose(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None


# ---- the SSE-facing feed facade ------------------------------------------------------------


class QuoteFeed:
    """What the `/quote/stream` route holds: registers a connection's symbols into the poller
    (respecting the per-connection cap), subscribes it to their channels, relays each published
    snapshot as an SSE frame, and — critically — decrements the demand refcount for every symbol
    on disconnect, so an abandoned symbol stops being polled (bounded observation)."""

    def __init__(
        self,
        *,
        poller: QuotePoller,
        transport: QuoteTransport,
        channel_prefix: str,
        max_symbols_per_connection: int,
    ) -> None:
        self._poller = poller
        self._transport = transport
        self._channel_prefix = channel_prefix
        self._max_per_conn = max_symbols_per_connection

    @property
    def poller(self) -> QuotePoller:
        return self._poller

    async def stream(
        self,
        symbols: Iterable[str],
        *,
        market: Market,
        cadence: Cadence,
        transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
    ) -> AsyncIterator[str]:
        """Yield SSE frames for `symbols`. Over-cap or globally-rejected symbols are skipped
        (the connection keeps serving the ones that fit), and on exit every accepted symbol's
        refcount is decremented — no leaked demand that would keep the poller alive forever.

        `transform` is the per-connection masking hook: the shared poll publishes the real
        snapshot to the channel once, and each connection applies its own entitlement decision to
        the frame data before it goes out — so masking stays per-connection (a Free and a Premium
        watcher of the same symbol share one upstream fetch but see the masking their tier
        warrants), while the fetch is shared. `None` = pass through unchanged (public data)."""
        # Per-connection cap: never register more than the ceiling from one connection.
        wanted = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))[: self._max_per_conn]
        accepted: list[str] = []
        for symbol in wanted:
            if await self._poller.add_subscriber(market, symbol, cadence):
                accepted.append(symbol)
        channels = [channel_for(market, s, prefix=self._channel_prefix) for s in accepted]
        # A priming comment tells the client (and a test) the subscription is live before any
        # tick has fired — SSE comment lines (starting ':') are ignored by the EventSource parser.
        yield f": subscribed {len(accepted)}\n\n"
        subscription = self._transport.subscribe(channels) if channels else _empty()
        try:
            async for event in subscription:
                data = transform(dict(event.data)) if transform is not None else dict(event.data)
                yield _format_quote_frame(event.seq, event.kind, data)
        finally:
            for symbol in accepted:
                await self._poller.remove_subscriber(market, symbol, cadence)


async def _empty() -> AsyncIterator[Event]:
    """A never-yielding stream for a connection whose every symbol was rejected — it holds open
    and cleans up like any other rather than erroring."""
    await asyncio.Event().wait()
    yield Event(kind="quote", seq=0, ts=datetime.now(UTC))  # pragma: no cover — after the wait


def _format_quote_frame(seq: int, kind: str, data: dict[str, object]) -> str:
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"id: {seq}\nevent: {kind}\ndata: {body}\n\n"


__all__ = [
    "Cadence",
    "DemandRegistry",
    "InMemoryDemandRegistry",
    "InMemoryQuoteTransport",
    "MarketOpenGate",
    "QuoteFeed",
    "QuoteFetchResult",
    "QuoteFetcher",
    "QuotePoller",
    "QuoteTransport",
    "RedisDemandRegistry",
    "RedisQuoteTransport",
    "channel_for",
]
