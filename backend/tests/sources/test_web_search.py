"""web_search (Brave) adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.web_search as web_search_module
from config.settings import Settings
from schemas.enums import DataClass, TLSPolicy
from schemas.source import HealthStatus
from sources.web_search import WebSearchAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _settings(key: str | None = "test-brave-key") -> Settings:
    return Settings(brave_search_api_key=key)


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(web_search_module, "build_async_client", make_json_client_factory(body))


def test_validate_rejects_non_object_web_field() -> None:
    adapter = WebSearchAdapter(settings=_settings())
    with pytest.raises(ValueError, match="not an object"):
        adapter.validate({"web": "nope"})


async def test_search_without_key_is_unavailable_tier() -> None:
    adapter = WebSearchAdapter(settings=_settings(key=None))

    result = await adapter.search("2330 台積電", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_search_returns_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("web_search", "search_2330.json")
    _patch_client(monkeypatch, body)
    adapter = WebSearchAdapter(settings=_settings())

    result = await adapter.search("2330 台積電", tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "WEB"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    hits = result.payload["hits"]
    assert isinstance(hits, list)
    assert len(hits) == 2
    assert hits[0]["url"].startswith("https://")  # type: ignore[index]


async def test_fetch_url_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="hello world")

        return httpx.AsyncClient(transport=httpx.MockTransport(responder))

    monkeypatch.setattr(web_search_module, "build_async_client", factory)
    adapter = WebSearchAdapter(settings=_settings())

    result = await adapter.fetch_url("https://example.com/article", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["text"] == "hello world"
