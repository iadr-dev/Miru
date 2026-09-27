"""The `dataaccess/` seam for `get_macro_calendar` — the one legitimate place `tools/` can reach
`sources.financecalendar.FinanceCalendarAdapter` from.

`FINANCE_CALENDAR` has no `(capability, market)` entry in `DataAccessRouter`'s priority config (a
calendar query has no symbol to route), so this tool cannot go through `DataAccessRouter.fetch()`
— the same non-router carve-out `dataaccess/news.py::NewsGateway` already establishes for `NEWS`.
"""

from __future__ import annotations

from schemas.enums import TLSPolicy
from schemas.source import SourceResult
from sources.financecalendar import FinanceCalendarAdapter


class FinanceCalendarGateway:
    """Thin `dataaccess/`-layer wrapper around `FinanceCalendarAdapter` for `get_macro_calendar`."""

    def __init__(self, adapter: FinanceCalendarAdapter | None = None) -> None:
        self._adapter = adapter or FinanceCalendarAdapter()

    async def fetch_calendar(
        self,
        *,
        impact: str | None = None,
        days_ahead: int = 14,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return await self._adapter.fetch_calendar(
            impact=impact, days_ahead=days_ahead, tls_policy=tls_policy
        )


__all__ = ["FinanceCalendarGateway"]
