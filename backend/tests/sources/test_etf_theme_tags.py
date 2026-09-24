"""ETF_THEME_TAGS adapter tests — recorded HTML fixtures, no live network (default profile)."""

from __future__ import annotations

import httpx
import pytest

import sources.etf_theme_tags as etf_theme_tags_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.etf_theme_tags import EtfThemeTagsAdapter, _parse_theme_tags

_PAGE_WITH_TWO_TAGS = """
<div class="etf-result">
    <div class="text">主題/因子：</div>

        <span class="result"><a href="/zh/ETFortune/products?tag=%E7%A7%91%E6%8A%80%E4%B8%BB%E9%A1%8C%E5%9E%8B">科技主題型</a></span>

        <span class="result"><a href="/zh/ETFortune/products?tag=%E9%AB%98%E8%82%A1%E6%81%AF">高股息</a></span>

</div>
"""

_PAGE_WITH_NO_TAGS = """
<div class="etf-result">
    <div class="text">主題/因子：</div>

</div>
"""

_PAGE_WITHOUT_BLOCK = "<html><body>not an etf page</body></html>"


def _client_factory(response_text: str, *, status_code: int = 200) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, text=response_text)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_supports_declares_tw_etf_theme_tags() -> None:
    adapter = EtfThemeTagsAdapter()
    assert adapter.supports() == {(Capability.ETF_THEME_TAGS, Market.TW)}


def test_parse_theme_tags_extracts_every_tag_in_the_result_block() -> None:
    assert _parse_theme_tags(_PAGE_WITH_TWO_TAGS) == ("科技主題型", "高股息")


def test_parse_theme_tags_empty_tuple_when_block_present_but_no_tags() -> None:
    assert _parse_theme_tags(_PAGE_WITH_NO_TAGS) == ()


def test_parse_theme_tags_none_when_block_entirely_absent() -> None:
    assert _parse_theme_tags(_PAGE_WITHOUT_BLOCK) is None


async def test_fetch_returns_real_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        etf_theme_tags_module, "build_async_client", _client_factory(_PAGE_WITH_TWO_TAGS)
    )
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="00929", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_THEME_TAGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "ETF_THEME_TAGS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    assert result.payload["tags"] == ["科技主題型", "高股息"]


async def test_fetch_zero_tags_is_ok_not_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real ETF with no 主題/因子 assigned is a genuinely valid OK result with empty tags —
    distinct from a symbol not found on the microsite at all (STALE, below)."""
    monkeypatch.setattr(
        etf_theme_tags_module, "build_async_client", _client_factory(_PAGE_WITH_NO_TAGS)
    )
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_THEME_TAGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["tags"] == []


async def test_fetch_unknown_symbol_returns_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        etf_theme_tags_module, "build_async_client", _client_factory(_PAGE_WITHOUT_BLOCK)
    )
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="NOPE", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_THEME_TAGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_non_tw_market_returns_unavailable_tier() -> None:
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="SPY", market=Market.US)

    result = await adapter.fetch(Capability.ETF_THEME_TAGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_degrades_honestly_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            raise httpx.ConnectError("boom")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(etf_theme_tags_module, "build_async_client", factory)
    adapter = EtfThemeTagsAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_THEME_TAGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
