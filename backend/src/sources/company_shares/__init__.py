"""Shares outstanding of the listed and OTC companies (TWSE + TPEx company basics), free.

The input of the derived market-value datasets (`ingest/jobs/market_weight.py`).
"""

from __future__ import annotations

from .adapter import SOURCE_ID, CompanySharesAdapter, SharesSnapshot
from .parse import LISTED, OTC, ShareEntry

__all__ = ["LISTED", "OTC", "SOURCE_ID", "CompanySharesAdapter", "ShareEntry", "SharesSnapshot"]
