"""Tests for `YFinanceLiveQuoteAdapter` against an injected fake `YFinanceLiveQuoteManager`-
shaped stub — no real yfinance/network touch. `anyio.to_thread.run_sync` is patched to stub the
`.info` snapshot fallback (`_fetch_info_blocking`) the same way `tests/sources/yfinance/
_helpers.py::patch_run_sync` does for `YFinanceAdapter`'s own tests."""

from __future__ import annotations

import anyio.to_thread
import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance.live_quote_adapter import YFinanceLiveQuoteAdapter


def _patch_info(monkeypatch: pytest.MonkeyPatch, info: dict[str, object]) -> None:
    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        del fn, args
        return info

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


class _FakeManager:
    def __init__(self, *, tick: dict[str, object] | None = None) -> None:
        self._tick = tick
        self.subscribed: list[str] = []

    async def subscribe(self, yahoo_symbol: str) -> None:
        self.subscribed.append(yahoo_symbol)

    def latest(self, yahoo_symbol: str) -> dict[str, object] | None:
        del yahoo_symbol
        return self._tick


def test_supports_declares_only_us_live_price_quote() -> None:
    adapter = YFinanceLiveQuoteAdapter(manager=_FakeManager())
    assert adapter.supports() == {(Capability.LIVE_PRICE_QUOTE, Market.US)}


async def test_fetch_wrong_capability_returns_unavailable_tier() -> None:
    adapter = YFinanceLiveQuoteAdapter(manager=_FakeManager())
    result = await adapter.fetch(
        Capability.PRICE_EOD, Symbol(id="AAPL", market=Market.US), tls_policy=TLSPolicy.STRICT
    )
    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_wrong_market_returns_unavailable_tier() -> None:
    adapter = YFinanceLiveQuoteAdapter(manager=_FakeManager())
    result = await adapter.fetch(
        Capability.LIVE_PRICE_QUOTE,
        Symbol(id="2330", market=Market.TW),
        tls_policy=TLSPolicy.STRICT,
    )
    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_no_tick_yet_falls_back_to_info_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No streamed tick (e.g. market closed) must not surface an empty price — falls back to a
    one-shot `.info` snapshot carrying the last regular-session price."""
    _patch_info(
        monkeypatch,
        {
            "regularMarketPrice": 335.4,
            "previousClose": 335.92,
            "regularMarketChangePercent": -0.15480457,
            "regularMarketDayHigh": 336.8,
            "regularMarketDayLow": 334.53,
            "regularMarketVolume": 3467242,
            "regularMarketOpen": 336.04,
            "currency": "USD",
        },
    )
    manager = _FakeManager(tick=None)
    adapter = YFinanceLiveQuoteAdapter(manager=manager)
    result = await adapter.fetch(
        Capability.LIVE_PRICE_QUOTE,
        Symbol(id="AAPL", market=Market.US),
        tls_policy=TLSPolicy.STRICT,
    )
    assert result.health.status is HealthStatus.STALE  # honestly not a live tick
    tick = result.payload["tick"]  # type: ignore[index]
    assert tick["price"] == pytest.approx(335.4)
    assert tick["previous_close"] == pytest.approx(335.92)
    assert tick["change_percent"] == pytest.approx(-0.15480457)
    assert manager.subscribed == ["AAPL"]


async def test_fetch_no_tick_and_no_snapshot_returns_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_info(monkeypatch, {})
    manager = _FakeManager(tick=None)
    adapter = YFinanceLiveQuoteAdapter(manager=manager)
    result = await adapter.fetch(
        Capability.LIVE_PRICE_QUOTE,
        Symbol(id="DELISTEDX", market=Market.US),
        tls_policy=TLSPolicy.STRICT,
    )
    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_with_tick_narrows_raw_ws_message_to_known_fields() -> None:
    """The raw decoded `PricingData` WS message carries fields `LivePriceTick` (`extra="forbid"`)
    doesn't declare (`id`/`time`/`exchange`/`quote_type`/`last_size`/`price_hint`) — confirmed
    live these DO appear on a real tick and, unfiltered, blow up `model_validate` at the tool
    layer with a `500`. The adapter must narrow to the known field set before returning."""
    tick = {
        "id": "AAPL",
        "time": "1790346697000",
        "exchange": "NMS",
        "quote_type": 8,
        "price": 230.5,
        "change_percent": 1.2,
        "last_size": "40",
        "price_hint": "2",
    }
    manager = _FakeManager(tick=tick)
    adapter = YFinanceLiveQuoteAdapter(manager=manager)
    result = await adapter.fetch(
        Capability.LIVE_PRICE_QUOTE,
        Symbol(id="AAPL", market=Market.US),
        tls_policy=TLSPolicy.STRICT,
    )
    assert result.health.status is HealthStatus.OK
    assert result.payload == {"tick": {"price": 230.5, "change_percent": 1.2}}
    assert result.currentness_caveat
