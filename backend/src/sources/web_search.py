"""web_search adapter — Brave Search API, provider-agnostic web search + fetch.

Not a `(capability, market)` router citizen in the same sense as market-data adapters — `WEB`
has no `Capability.NEWS`/etc mapping in `source_priority.yaml` today (the `web_search` tool
calls it directly, outside the router's capability list). It still implements
`SourceAdapter`-shaped `search()`/`fetch()` here so all network I/O stays inside `sources/`
and TLS goes through the one chokepoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict, cast

import httpx
import structlog

from config.settings import Settings, get_settings
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

_CAVEAT = (
    "Brave Search API result — third-party web content, live but unverified; cite the "
    "source, never restate as our own analysis or a trading signal."
)


class WebSearchHit(TypedDict):
    title: str
    url: str
    snippet: str
    published: str | None


class WebSearchAdapter:
    """The `WEB` source adapter (Brave Search)."""

    source_id = "WEB"
    timing_class = TimingClass.REFERENCE

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        web = raw.get("web")
        if web is not None and not isinstance(web, dict):
            raise ValueError("Brave Search response 'web' field is not an object")
        return raw

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        """`search(query) -> [{title, url, snippet, published}]`."""
        key = self._settings.brave_search_api_key
        if not key:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail="BRAVE_SEARCH_API_KEY not configured",
                ),
            )

        headers = {"Accept": "application/json", "X-Subscription-Token": key}
        try:
            async with build_async_client(tls_policy=tls_policy, headers=headers) as client:
                response = await client.get(_SEARCH_URL, params={"q": query, "count": str(count)})
                response.raise_for_status()
                raw: dict[str, object] = response.json()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        try:
            validated = self.validate(raw)
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        web = validated.get("web")
        results: object = (
            cast("dict[str, object]", web).get("results", []) if isinstance(web, dict) else []
        )
        hits: list[WebSearchHit] = []
        if isinstance(results, list):
            untyped_results = cast("list[object]", results)
            for raw_item in untyped_results:
                if not isinstance(raw_item, dict):
                    continue
                item = cast("dict[str, object]", raw_item)
                age = item.get("age")
                hits.append(
                    WebSearchHit(
                        title=str(item.get("title", "")),
                        url=str(item.get("url", "")),
                        snippet=str(item.get("description", "")),
                        published=age if isinstance(age, str) else None,
                    )
                )
        status = HealthStatus.OK if hits else HealthStatus.STALE
        detail = "ok" if hits else "no results"
        return _result(
            payload={"hits": [dict(hit) for hit in hits]},
            health=SourceHealth(status=status, detail=detail),
        )

    async def fetch_url(
        self, url: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """`fetch(url) -> text` — plain-text extraction of an external page."""
        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(url)
                response.raise_for_status()
                text = response.text
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )
        return _result(
            payload={"url": url, "text": text},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=WebSearchAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["WebSearchAdapter", "WebSearchHit"]
