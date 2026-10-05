"""The ingest process's real `data.updated` publisher, built outside the ingest layer.

`ingest/` may not import `events/` (the one-way dependency rule: ingest sits below the interaction
layer), so it cannot construct the Redis transport itself — it declares the `DataUpdatePublisher`
seam and defaults to a no-op. The composition that binds a concrete publisher therefore has to live
above it; this is that factory. The ingest process finds it by *config name*
(`Settings.ingest_data_update_publisher_factory`, resolved in `ingest.publisher_binding`), the same
"implementation chosen by config" idiom the rest of the codebase uses for seams, so `ingest` never
imports it statically.
"""

from __future__ import annotations

from config.settings import Settings
from events.live_quote_factory import build_data_update_broker
from ingest.data_updates import DataUpdatePublisher


def build_data_update_publisher(settings: Settings) -> DataUpdatePublisher | None:
    """The cross-process publisher, or None when there is nothing to publish to.

    Only the Redis event bus crosses process boundaries. With the in-memory bus the ingest process
    would publish to a bus nobody else can read (the API and scheduler run in other processes), so
    binding it would look wired while delivering nothing — None keeps that honest: the caller falls
    back to the no-op and consumers rely on their own revalidation/sweep."""
    if settings.event_bus_backend != "redis":
        return None
    return build_data_update_broker(settings).publisher


__all__ = ["build_data_update_publisher"]
