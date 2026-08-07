"""PTT adapter tests — recorded HTML fixtures, no live network (default profile)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import sources.ptt as ptt_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.ptt import PttAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ptt"


def _client_factory(html: str, status_code: int = 200) -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_supports_declares_tw_news() -> None:
    adapter = PttAdapter()
    assert (Capability.NEWS, Market.TW) in adapter.supports()


def test_validate_rejects_missing_posts() -> None:
    adapter = PttAdapter()
    with pytest.raises(ValueError, match="missing 'posts'"):
        adapter.validate({})


async def test_fetch_board_stock_tags_are_stock_buzz(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "PTT"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    posts = result.payload["posts"]
    assert isinstance(posts, list)
    # The deleted post (no <a> link) must never surface as a record.
    assert len(posts) == 3
    assert all(post["board_tag"] == "stock_buzz" for post in posts)  # type: ignore[index]
    tags = {post["post_tag"] for post in posts}  # type: ignore[index]
    assert tags == {"[標的]", "[新聞]", "[心得]"}
    push_counts = {post["push_count"] for post in posts}  # type: ignore[index]
    assert "24" in push_counts
    assert "爆" in push_counts


async def test_fetch_board_beauty_tags_are_offtopic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beauty/Gossiping results must carry `offtopic` — load-bearing for the guardrail that
    keeps them out of any market claim."""
    html = (FIXTURES / "beauty_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Beauty", tls_policy=TLSPolicy.STRICT)

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    assert len(posts) == 2
    assert all(post["board_tag"] == "offtopic" for post in posts)  # type: ignore[index]
    assert "mascot" in result.currentness_caveat or "off-topic" in result.currentness_caveat


async def test_stock_and_beauty_tags_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    stock_html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    beauty_html = (FIXTURES / "beauty_index.html").read_text(encoding="utf-8")

    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(stock_html))
    adapter = PttAdapter()
    stock_result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(beauty_html))
    beauty_result = await adapter.fetch_board(board="Beauty", tls_policy=TLSPolicy.STRICT)

    stock_posts = stock_result.payload["posts"]
    beauty_posts = beauty_result.payload["posts"]
    assert isinstance(stock_posts, list)
    assert isinstance(beauty_posts, list)
    stock_tags = {post["board_tag"] for post in stock_posts}  # type: ignore[index]
    beauty_tags = {post["board_tag"] for post in beauty_posts}  # type: ignore[index]
    assert stock_tags == {"stock_buzz"}
    assert beauty_tags == {"offtopic"}
    assert stock_tags.isdisjoint(beauty_tags)


async def test_fetch_board_rejects_unknown_board() -> None:
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="NotABoard", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_board_degrades_honestly_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


async def test_fetch_dispatches_via_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT, board="Stock")

    assert result.health.status is HealthStatus.OK


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = PttAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}
