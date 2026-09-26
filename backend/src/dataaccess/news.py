"""The `dataaccess/` seam for `get_news_feed` — the one legitimate place `tools/` can reach
`sources.news.NewsAdapter` from.

`NEWS` has no `(capability, market)` entry in `DataAccessRouter`'s priority config (a query
string isn't `(capability, symbol)`-shaped), so this tool cannot go through
`DataAccessRouter.fetch()`. The architecture layering rule is still "`tools/` never imports
`sources/` directly" — this thin wrapper is the seam that satisfies that rule, the same shape
`dataaccess/web_search.py`'s `WebSearchGateway` already establishes for `WEB`.
"""

from __future__ import annotations

from schemas.enums import Market, TLSPolicy
from schemas.source import SourceResult
from sources.news import NewsAdapter


class NewsGateway:
    """Thin `dataaccess/`-layer wrapper around `NewsAdapter` for `get_news_feed`."""

    def __init__(self, adapter: NewsAdapter | None = None) -> None:
        self._adapter = adapter or NewsAdapter()

    async def fetch_news(
        self,
        *,
        query: str,
        market: Market = Market.TW,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return await self._adapter.fetch_news(query=query, market=market, tls_policy=tls_policy)


__all__ = ["NewsGateway"]
