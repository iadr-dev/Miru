"""One place for the redis-py Pub/Sub clean-up the event transports share."""

from __future__ import annotations

from redis.asyncio.client import PubSub


async def close_pubsub(pubsub: PubSub) -> None:
    """Close a Pub/Sub connection. redis-py's `PubSub.aclose` has no type annotations, so the
    suppression lives here instead of at every call site."""
    await pubsub.aclose()  # type: ignore[no-untyped-call]


__all__ = ["close_pubsub"]
