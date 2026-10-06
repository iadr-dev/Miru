"""The `dataaccess/` seam for `web_search`/`fetch_url` — the one legitimate place `tools/` can
reach `sources.web_search.WebSearchAdapter` from.

`WEB` has no `(capability, market)` entry in `DataAccessRouter`'s priority config (a query/URL
isn't `(capability, symbol)`-shaped — same rationale as the yfinance screener carve-out), so
these two tools cannot go through `DataAccessRouter.fetch()`. The architecture layering rule is
still "`tools/` never imports `sources/` directly" — this thin wrapper is the seam that
satisfies that rule the same way `DataAccessRouter` itself is the seam for capability-shaped
reads: `tools/` depends on `dataaccess/`, `dataaccess/` is the only caller of `sources/` outside
`ingest/`.
"""

from __future__ import annotations

from schemas.enums import TLSPolicy
from schemas.source import SourceResult
from sources.web_search import Resolver, WebSearchAdapter


class WebSearchGateway:
    """Thin `dataaccess/`-layer wrapper around `WebSearchAdapter` for the two web-access tools."""

    def __init__(self, adapter: WebSearchAdapter | None = None) -> None:
        self._adapter = adapter or WebSearchAdapter()

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        return await self._adapter.search(query, tls_policy=tls_policy, count=count)

    async def fetch_url(
        self, url: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        return await self._adapter.fetch_url(url, tls_policy=tls_policy)

    async def fetch_page(
        self,
        url: str,
        *,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        resolver: Resolver | None = None,
    ) -> SourceResult:
        if resolver is None:
            return await self._adapter.fetch_page(url, tls_policy=tls_policy)
        return await self._adapter.fetch_page(url, tls_policy=tls_policy, resolver=resolver)


__all__ = ["WebSearchGateway"]
