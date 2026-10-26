"""Characterization of `NewsAdapter.fetch_news` health/detail composition: partial failures,
per-source error strings, empty results, and which upstreams are hit per market. Per-URL stub
transport, recorded fixtures, no live network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

import sources.news as news_module
from config.settings import Settings
from schemas.enums import Market, TLSPolicy
from schemas.source import HealthStatus
from sources.news import NewsAdapter, RssFeed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"
GOOD_FEED = RssFeed(source="good_feed", url="https://good.example/rss")
BAD_FEED = RssFeed(source="bad_feed", url="https://bad.example/rss")
BAD_FEED_2 = RssFeed(source="bad_feed_2", url="https://bad2.example/rss")
EMPTY_FEED = RssFeed(source="empty_feed", url="https://empty.example/rss")
_EMPTY_RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


def _install(
    monkeypatch: pytest.MonkeyPatch, *, finnhub: Callable[[], httpx.Response] | None = None
) -> list[str]:
    """Route by host: good/empty feeds answer, bad feeds 500, Finnhub answers via `finnhub`."""
    requested: list[str] = []
    good = (FIXTURES / "cnyes_rss.xml").read_bytes()
    finnhub_ok = json.loads((FIXTURES / "finnhub.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if "finnhub.io" in url:
            return finnhub() if finnhub is not None else httpx.Response(200, json=finnhub_ok)
        if "good.example" in url:
            return httpx.Response(200, content=good)
        if "empty.example" in url:
            return httpx.Response(200, content=_EMPTY_RSS)
        return httpx.Response(500, text="boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_module, "build_async_client", factory)
    return requested


def _adapter(*feeds: RssFeed, key: str | None = None) -> NewsAdapter:
    return NewsAdapter(settings=Settings(finnhub_key=key), rss_feeds=feeds)


async def test_one_failing_feed_among_good_ones_is_ok_with_a_partial_failure_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    result = await _adapter(BAD_FEED, GOOD_FEED).fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    assert result.health.detail == "ok (partial failures: bad_feed: HTTPStatusError)"
    items = result.payload["items"]
    assert isinstance(items, list) and items
    assert {item["source"] for item in items} == {"good_feed"}  # type: ignore[index]


async def test_a_failing_finnhub_call_is_a_partial_failure_when_rss_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, finnhub=lambda: httpx.Response(429, text="slow down"))

    result = await _adapter(GOOD_FEED, key="k").fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    assert result.health.detail == "ok (partial failures: finnhub: HTTPStatusError)"
    items = result.payload["items"]
    assert isinstance(items, list)
    assert not any(str(item["source"]).startswith("finnhub:") for item in items)  # type: ignore[index]


async def test_every_source_failing_joins_the_errors_in_fetch_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, finnhub=lambda: httpx.Response(500))

    result = await _adapter(BAD_FEED, BAD_FEED_2, key="k").fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == (
        "bad_feed: HTTPStatusError; bad_feed_2: HTTPStatusError; finnhub: HTTPStatusError"
    )
    assert result.payload == {}


async def test_no_items_and_no_errors_is_stale_with_an_empty_items_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    result = await _adapter(EMPTY_FEED).fetch_news(query="2330", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.health.detail == "no items returned"
    assert result.payload == {"items": []}


async def test_no_items_with_an_error_is_unreachable_not_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    result = await _adapter(BAD_FEED, EMPTY_FEED).fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "bad_feed: HTTPStatusError"
    assert result.payload == {}


async def test_stale_with_only_a_finnhub_error_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, finnhub=lambda: httpx.Response(503))

    result = await _adapter(EMPTY_FEED, key="k").fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "finnhub: HTTPStatusError"


async def test_non_tw_market_without_a_key_makes_no_request_and_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = _install(monkeypatch)

    result = await _adapter(GOOD_FEED).fetch_news(
        query="x", market=Market.US, tls_policy=TLSPolicy.STRICT
    )

    assert requested == []
    assert result.health.status is HealthStatus.STALE
    assert result.payload == {"items": []}


async def test_non_tw_finnhub_failure_is_unreachable_and_never_touches_rss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = _install(monkeypatch, finnhub=lambda: httpx.Response(500))

    result = await _adapter(GOOD_FEED, key="k").fetch_news(
        query="x", market=Market.US, tls_policy=TLSPolicy.STRICT
    )

    assert len(requested) == 1 and "finnhub.io/api/v1/news" in requested[0]
    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "finnhub: HTTPStatusError"


async def test_tw_company_news_is_scoped_by_the_query_and_items_are_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = _install(monkeypatch)

    result = await _adapter(GOOD_FEED, key="k").fetch_news(
        query="2330", tls_policy=TLSPolicy.STRICT
    )

    finnhub_urls = [u for u in requested if "finnhub.io" in u]
    assert len(finnhub_urls) == 1
    assert "company-news" in finnhub_urls[0] and "symbol=2330" in finnhub_urls[0]
    items = result.payload["items"]
    assert isinstance(items, list)
    stamps = [item["published_at"] for item in items]  # type: ignore[index]
    assert stamps == sorted(stamps, reverse=True)
    assert result.health.detail == "ok"
