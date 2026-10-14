"""The batch-completion invalidation seam the ingest leader publishes on.

When a derived-compute batch lands, the leader emits a lightweight `data.updated` signal carrying
`{capability, market, as_of, scope}` so API workers can tell a subscribed client to revalidate
exactly the affected cache keys the moment fresh data exists — instead of waiting out a fixed
time boundary. Only the leader publishes (the same `LeaderElection.is_leader()` gate that already
guards the write itself); a follower never emits.

This is the *seam*, defined here in the ingest layer so the write path can depend on it without
importing the interaction layer (the dependency rule is one-way). The concrete cross-process
backend (Redis pub/sub, or an in-process stream for the single-process default) lives in
`events/` and satisfies this Protocol structurally — chosen by config at wiring time. The default
is `NoOpDataUpdatePublisher`, so an ingest run with no publisher wired simply skips the signal
(the boundary-anchored revalidate remains the correct floor) rather than failing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from schemas.enums import Capability, Market

logger = structlog.get_logger(__name__)


@runtime_checkable
class DataUpdatePublisher(Protocol):
    """Publish one `data.updated` invalidation signal. `scope` names what changed beyond the
    capability+market (e.g. "whole-market", a date, or a symbol subset) so the client can scope
    its revalidation; `as_of` is the landed batch's own currency, not the publish time."""

    async def publish_data_updated(
        self,
        *,
        capability: Capability,
        market: Market,
        as_of: str,
        scope: str,
    ) -> None: ...


class NoOpDataUpdatePublisher:
    """The default: no invalidation transport wired, so publishing is a no-op. An ingest run
    still lands its partition; clients fall back to boundary-anchored revalidation."""

    async def publish_data_updated(
        self,
        *,
        capability: Capability,
        market: Market,
        as_of: str,
        scope: str,
    ) -> None:
        del capability, market, as_of, scope


class CoalescingDataUpdatePublisher:
    """Holds the signals back and publishes each `(capability, market, scope)` once, at its newest
    `as_of`, on `flush()`. For a backfill: it writes thousands of partitions oldest first (about
    11,000 for three years), and every one used to publish its own signal, which every open browser
    tab and the scheduler's alert engine then received. A consumer only needs to know that data up
    to the newest date has landed."""

    def __init__(self, inner: DataUpdatePublisher) -> None:
        self._inner = inner
        self._latest: dict[tuple[Capability, Market, str], str] = {}

    async def publish_data_updated(
        self,
        *,
        capability: Capability,
        market: Market,
        as_of: str,
        scope: str,
    ) -> None:
        key = (capability, market, scope)
        current = self._latest.get(key)
        if current is None or as_of > current:  # ISO dates order as strings
            self._latest[key] = as_of

    async def flush(self) -> int:
        """Publish what is held, newest date per key, and forget it. Returns how many were sent."""
        held, self._latest = self._latest, {}
        for (capability, market, scope), as_of in held.items():
            await self._inner.publish_data_updated(
                capability=capability, market=market, as_of=as_of, scope=scope
            )
        if held:
            logger.info("data_updated_coalesced_flush", signals=len(held))
        return len(held)


__all__ = ["CoalescingDataUpdatePublisher", "DataUpdatePublisher", "NoOpDataUpdatePublisher"]
