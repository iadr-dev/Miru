"""The `dataaccess/` seam for `sources.yfinance.run_screener` — the query-shaped carve-out that,
like `web_search`/`fetch_url`, cannot go through `DataAccessRouter.fetch(cap, sym, ...)`
because a screener query has no symbol to route (see `sources/yfinance/screener.py`'s own
docstring). This thin wrapper is what lets `tools/` reach it without importing `sources/`
directly, mirroring `dataaccess/web_search.py`'s identical role for the other non-router
carve-out.
"""

from __future__ import annotations

from schemas.screener import ScreenerQuery, ScreenerResult
from sources.yfinance import run_predefined_screener as _run_predefined_screener
from sources.yfinance import run_screener as _run_screener


async def run_equity_screener(query: ScreenerQuery) -> ScreenerResult:
    return await _run_screener(query)


async def run_predefined_equity_screener(name: str, size: int) -> ScreenerResult:
    return await _run_predefined_screener(name, size)


__all__ = ["run_equity_screener", "run_predefined_equity_screener"]
