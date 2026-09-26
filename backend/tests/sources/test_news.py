"""News adapter tests — recorded RSS/Finnhub fixtures, no live network (default profile).

GDELT was removed from `NewsAdapter` (2026-09, see `sources/news.py`'s own docstring) after live
verification found its public endpoint persistently rate-limited — these tests no longer exercise
it."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import sources.news as news_module
from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.news import NewsAdapter, RssFeed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"


def _settings(finnhub_key: str | None = None) -> Settings:
    return Settings(finnhub_key=finnhub_key)


def _client_factory(rss_body: bytes, finnhub_body: object | None) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "finnhub.io" in url:
            if finnhub_body is None:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=finnhub_body)
        return httpx.Response(200, content=rss_body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_supports_declares_news_for_all_markets() -> None:
    adapter = NewsAdapter(settings=_settings())
    supported = adapter.supports()
    assert (Capability.NEWS, Market.TW) in supported
    assert (Capability.NEWS, Market.US) in supported


def test_validate_rejects_missing_items() -> None:
    adapter = NewsAdapter(settings=_settings())
    with pytest.raises(ValueError, match="missing 'items'"):
        adapter.validate({})


async def test_fetch_news_merges_rss_and_finnhub(monkeypatch: pytest.MonkeyPatch) -> None:
    rss_body = (FIXTURES / "cnyes_rss.xml").read_bytes()
    finnhub_body = json.loads((FIXTURES / "finnhub.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(
        news_module,
        "build_async_client",
        _client_factory(rss_body, finnhub_body),
    )
    adapter = NewsAdapter(
        settings=_settings(finnhub_key="test-key"),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )

    result = await adapter.fetch_news(query="2330", tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "NEWS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    items = result.payload["items"]
    assert isinstance(items, list)
    sources = {item["source"] for item in items}  # type: ignore[index]
    assert "cnyes_tw_stock" in sources
    assert any(s.startswith("finnhub:") for s in sources)
    # Sorted newest-first.
    published = [item["published_at"] for item in items]  # type: ignore[index]
    assert published == sorted(published, reverse=True)


async def test_fetch_news_skips_finnhub_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    rss_body = (FIXTURES / "cnyes_rss.xml").read_bytes()

    monkeypatch.setattr(news_module, "build_async_client", _client_factory(rss_body, None))
    adapter = NewsAdapter(
        settings=_settings(finnhub_key=None),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )

    result = await adapter.fetch_news(query="2330", tls_policy=TLSPolicy.STRICT)

    items = result.payload["items"]
    assert isinstance(items, list)
    assert not any(str(item["source"]).startswith("finnhub:") for item in items)  # type: ignore[index]


async def test_fetch_news_degrades_honestly_when_all_feeds_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_module, "build_async_client", factory)
    adapter = NewsAdapter(
        settings=_settings(finnhub_key=None),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )

    result = await adapter.fetch_news(query="2330", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


async def test_fetch_dispatches_via_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    rss_body = (FIXTURES / "cnyes_rss.xml").read_bytes()
    monkeypatch.setattr(news_module, "build_async_client", _client_factory(rss_body, None))
    adapter = NewsAdapter(
        settings=_settings(),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = NewsAdapter(settings=_settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_news_us_market_skips_rss_and_uses_finnhub_general_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real bug this guards against: US news previously mixed in TW-only RSS feeds and
    called Finnhub's company-news endpoint with a free-text phrase as `symbol` (which Finnhub
    company-news requires to be a real ticker) — silently returning zero Finnhub items. For any
    non-TW market, RSS must be skipped entirely and Finnhub's general market-news category
    (no symbol/keyword) must be used instead."""
    finnhub_body = json.loads((FIXTURES / "finnhub.json").read_text(encoding="utf-8"))
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json=finnhub_body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_module, "build_async_client", factory)
    adapter = NewsAdapter(
        settings=_settings(finnhub_key="test-key"),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )

    result = await adapter.fetch_news(
        query="US stock market", market=Market.US, tls_policy=TLSPolicy.STRICT
    )

    # Only one request made at all — the RSS feed was never fetched.
    assert len(requested_urls) == 1
    assert "finnhub.io/api/v1/news" in requested_urls[0]
    assert "category=general" in requested_urls[0]
    assert "cnyes.com" not in requested_urls[0]
    items = result.payload["items"]
    assert isinstance(items, list)
    assert all(str(item["source"]).startswith("finnhub:") for item in items)  # type: ignore[index]


async def test_fetch_news_tw_market_still_uses_company_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the unchanged TW behavior: company-news (query-scoped), not general-news."""
    finnhub_body = json.loads((FIXTURES / "finnhub.json").read_text(encoding="utf-8"))
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if "finnhub.io" in str(request.url):
            return httpx.Response(200, json=finnhub_body)
        return httpx.Response(200, content=(FIXTURES / "cnyes_rss.xml").read_bytes())

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_module, "build_async_client", factory)
    adapter = NewsAdapter(
        settings=_settings(finnhub_key="test-key"),
        rss_feeds=(RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/fake"),),
    )

    await adapter.fetch_news(query="2330", market=Market.TW, tls_policy=TLSPolicy.STRICT)

    finnhub_urls = [u for u in requested_urls if "finnhub.io" in u]
    assert len(finnhub_urls) == 1
    assert "company-news" in finnhub_urls[0]
    assert "symbol=2330" in finnhub_urls[0]
