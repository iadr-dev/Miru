"""The `dataaccess/` seam for `get_us_market_index_strip`'s live index-ticker quotes — the one
legitimate place `tools/` can reach `sources.yfinance.fetch_index_quote` from (a plain live
fetch with no `(capability, symbol)` entry, same non-router carve-out shape
`dataaccess/screener.py`/`dataaccess/news.py` already establish for their own seams).
"""

from __future__ import annotations

from sources.yfinance import fetch_index_quote as _fetch_index_quote


async def fetch_index_quote(yahoo_symbol: str) -> tuple[float | None, float | None]:
    return await _fetch_index_quote(yahoo_symbol)


__all__ = ["fetch_index_quote"]
