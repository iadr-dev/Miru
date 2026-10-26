"""Official company filings (monthly revenue, ...) from MOPS - the free first source for the
period-keyed ingest jobs."""

from __future__ import annotations

from .adapter import MOPS_MIN_GAP_SECONDS, SOURCE_ID, CompanyFilingsAdapter

__all__ = ["MOPS_MIN_GAP_SECONDS", "SOURCE_ID", "CompanyFilingsAdapter"]
