"""web_search adapter — Brave Search API, provider-agnostic web search + fetch.

Not a `(capability, market)` router citizen in the same sense as market-data adapters — `WEB`
has no `Capability.NEWS`/etc mapping in `source_priority.yaml` today (the `web_search` tool
calls it directly, outside the router's capability list). It still implements
`SourceAdapter`-shaped `search()`/`fetch()` here so all network I/O stays inside `sources/`
and TLS goes through the one chokepoint.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypedDict, cast
from urllib.parse import urljoin, urlsplit

import httpx
import structlog
from selectolax.parser import HTMLParser

from config.settings import Settings, get_settings
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from schemas.urlsafety import canonical_url, is_public_host
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

_CAVEAT = (
    "Brave Search API result — third-party web content, live but unverified; cite the "
    "source, never restate as our own analysis or a trading signal."
)


# Hardened page read (deep research): a model- or search-supplied URL is untrusted input.
_MAX_PAGE_BYTES = 1_000_000
_MAX_REDIRECTS = 4
_PAGE_TIMEOUT = 8.0
_READABLE_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_NOISE_TAGS = "script, style, noscript, nav, footer, aside, form, iframe, svg, header"
_USER_AGENT = "Mozilla/5.0 (compatible; MiruResearchBot/1.0)"

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class _UnsafeUrlError(Exception):
    """A URL (or a redirect hop) that must not be requested."""


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

    async def fetch_page(
        self,
        url: str,
        *,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        resolver: Resolver = _resolve,
    ) -> SourceResult:
        """Read one public web page as plain text, treating the URL as hostile.

        Redirects are followed by hand so **every hop** is re-validated; the host is resolved
        here and the request goes to the checked IP (TLS still verifies the original hostname via
        SNI), so DNS rebinding cannot swap in an internal address; the body is size-capped and
        only HTML/plain text is read.
        """
        try:
            async with build_async_client(
                tls_policy=tls_policy,
                timeout=_PAGE_TIMEOUT,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,text/plain;q=0.9",
                    "Accept-Encoding": "identity",
                },
            ) as client:
                final_url, body, content_type = await _read_following_redirects(
                    client, url, resolver
                )
        except _UnsafeUrlError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )
        title, text = _extract_text(body, content_type)
        return _result(
            payload={"url": final_url, "title": title, "text": text},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


async def _read_following_redirects(
    client: httpx.AsyncClient, url: str, resolver: Resolver
) -> tuple[str, bytes, str]:
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        safe = canonical_url(current)
        if safe is None:
            raise _UnsafeUrlError("blocked url")
        request_url, headers, extensions = await _pin_to_public_ip(safe, resolver)
        async with client.stream(
            "GET", request_url, headers=headers, extensions=extensions, follow_redirects=False
        ) as response:
            if response.status_code in _REDIRECT_STATUS:
                location = response.headers.get("location")
                if not location:
                    raise _UnsafeUrlError("redirect without location")
                current = urljoin(safe, location)
                continue
            content_type = _readable_type(response)  # headers vetted BEFORE any body is read
            return safe, await _read_capped_body(response), content_type
    raise _UnsafeUrlError("too many redirects")


def _readable_type(response: httpx.Response) -> str:
    response.raise_for_status()
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise _UnsafeUrlError("compressed body refused")  # no decompression bombs
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in _READABLE_TYPES:
        raise _UnsafeUrlError("unsupported content type")
    return str(content_type)


async def _read_capped_body(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > _MAX_PAGE_BYTES:
            chunks.append(chunk[: len(chunk) - (size - _MAX_PAGE_BYTES)])
            break
        chunks.append(chunk)
    return b"".join(chunks)


async def _pin_to_public_ip(
    url: str, resolver: Resolver
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve the host, require every address public, and address the request to the first."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = await resolver(host, port)
    except OSError as exc:
        raise _UnsafeUrlError("dns failure") from exc
    if not addresses or not all(is_public_host(address) for address in addresses):
        raise _UnsafeUrlError("blocked address")
    pinned = httpx.URL(url).copy_with(host=addresses[0])
    headers = {"Host": parts.netloc, "Accept-Encoding": "identity"}
    return str(pinned), headers, {"sni_hostname": host}


def _extract_text(body: bytes, content_type: str) -> tuple[str, str]:
    decoded = body.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        return "", decoded.strip()
    tree = HTMLParser(decoded)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node is not None else ""
    for node in tree.css(_NOISE_TAGS):
        node.decompose()
    root = tree.body or tree.root
    text = root.text(separator="\n", strip=True) if root is not None else ""
    return title, text


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
