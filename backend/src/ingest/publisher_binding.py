"""Bind the ingest process's `data.updated` publisher from config, without importing the layer that
provides it.

The concrete (Redis) publisher lives in `events/`, which `ingest/` may not import. Rather than
leave the ingest daemon on the no-op forever — which silently disabled the signal in every
deployed stack — the context builder resolves a factory by its `"module:callable"` name from
settings. Resolution is dynamic on purpose: the architecture test walks static imports, and this
is the sanctioned seam for a lower layer to be handed an implementation from a higher one.

Failure policy: ingest must always land its partitions. Any problem here — an empty setting, a
missing module, a factory that raises or returns something unusable — logs a warning and falls back
to the no-op publisher; a bad wiring never stops a data pull, and consumers still converge through
their own revalidation / reconciliation sweep.
"""

from __future__ import annotations

import importlib
from typing import cast

import structlog

from config.settings import Settings
from ingest.data_updates import DataUpdatePublisher, NoOpDataUpdatePublisher

logger = structlog.get_logger(__name__)


def resolve_data_update_publisher(settings: Settings) -> DataUpdatePublisher:
    """The publisher the ingest context should use — the configured one, or the no-op."""
    spec = settings.ingest_data_update_publisher_factory.strip()
    if not spec:
        return NoOpDataUpdatePublisher()
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        logger.warning("ingest_publisher_factory_malformed", spec=spec)
        return NoOpDataUpdatePublisher()
    try:
        factory = getattr(importlib.import_module(module_name), attr)
        built: object = factory(settings)
    except Exception:
        logger.exception("ingest_publisher_factory_failed", spec=spec)
        return NoOpDataUpdatePublisher()
    if built is None:
        logger.info("ingest_publisher_not_bound", spec=spec, reason="factory returned no publisher")
        return NoOpDataUpdatePublisher()
    if not callable(getattr(built, "publish_data_updated", None)):
        logger.warning("ingest_publisher_factory_unusable", spec=spec, got=type(built).__name__)
        return NoOpDataUpdatePublisher()
    logger.info("ingest_publisher_bound", spec=spec, publisher=type(built).__name__)
    return cast("DataUpdatePublisher", built)


__all__ = ["resolve_data_update_publisher"]
