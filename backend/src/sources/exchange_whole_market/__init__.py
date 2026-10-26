"""Official TWSE + TPEx whole-market-by-date reports - the free first source for the ingest jobs."""

from __future__ import annotations

from .adapter import (
    SOURCE_ID,
    TPEX_MIN_GAP_SECONDS,
    TWSE_MIN_GAP_SECONDS,
    ExchangeWholeMarketAdapter,
)

__all__ = [
    "SOURCE_ID",
    "TPEX_MIN_GAP_SECONDS",
    "TWSE_MIN_GAP_SECONDS",
    "ExchangeWholeMarketAdapter",
]
