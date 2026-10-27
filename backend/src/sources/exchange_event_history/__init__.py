"""Official TWSE + TPEx attention-stock and disposition history by date range (ingest backfill)."""

from __future__ import annotations

from .adapter import (
    TPEX_MIN_GAP_SECONDS,
    TWSE_MIN_GAP_SECONDS,
    EventHistory,
    ExchangeEventHistoryAdapter,
)

__all__ = [
    "TPEX_MIN_GAP_SECONDS",
    "TWSE_MIN_GAP_SECONDS",
    "EventHistory",
    "ExchangeEventHistoryAdapter",
]
