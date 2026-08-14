"""The `/quote/stream` and `/updates/stream` routes exercised through the REAL FastAPI surface —
the proof the poller and the `data.updated` broker are actually WIRED to their call sites, not
merely correct in isolation.

The load-bearing assertions:
- Two concurrent subscribers to the same symbol trigger exactly ONE upstream fetch (the whole
  value proposition of shared polling), and
- disconnecting a subscriber drops the demand refcount for real (bounded observation).
- A `data.updated` frame published by the REAL ingest batch-completion path is relayed by
  `/updates/stream`.

Driven with the ASGI SSE harness (`sse_harness`) rather than `TestClient`/`httpx`, both of which
buffer the whole response and so cannot read an unbounded SSE stream or disconnect mid-stream. The
harness invokes the real app object — real routing, dependency resolution, route handler, and the
route's own `StreamingResponse` generator including its disconnect-cleanup path.
"""

from __future__ import annotations

import asyncio
from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from events.data_updates import InMemoryDataUpdateBroker
from events.live_quote import (
    InMemoryDemandRegistry,
    InMemoryQuoteTransport,
    QuoteFeed,
    QuoteFetchResult,
    QuotePoller,
)
from events.live_quote_factory import DataUpdateBroker
from ingest.context import IngestContext
from ingest.jobs import job_price_eod
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Market, TLSPolicy
from sources.finmind import FinMindAdapter
from tests.api.conftest import _TEST_JWT_SECRET, build_sponsor_registry, build_test_app
from tests.api.sse_harness import collect_sse, frames_of_kind, quote_frames
from tests.sources.conftest import load_fixture, make_json_client_factory


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")


class _CountingFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult:
        self.calls.append((market.value, symbol))
        return QuoteFetchResult(
            quote={"bar": {"trade_date": "2024-01-02", "close": 101.0}}, as_of="2024-01-02"
        )


class _AlwaysOpen:
    def is_open(self, market: Market, now: object) -> bool:
        del market, now
        return True


def _feed(fetcher: _CountingFetcher, registry: InMemoryDemandRegistry) -> QuoteFeed:
    transport = InMemoryQuoteTransport()
    poller = QuotePoller(
        registry=registry,
        transport=transport,
        fetcher=fetcher,
        market_gate=_AlwaysOpen(),
        channel_prefix="quote",
        # Fast cadence so the test doesn't wait seconds; the warm-up sleep before the first poll
        # still lets both subscribers register before that first (shared) fetch fires.
        cadence_seconds={"normal": 0.05, "saver": 0.2},
    )
    return QuoteFeed(
        poller=poller, transport=transport, channel_prefix="quote", max_symbols_per_connection=50
    )


async def test_two_subscribers_same_symbol_share_one_fetch(settings: Settings) -> None:
    fetcher = _CountingFetcher()
    registry = InMemoryDemandRegistry(max_symbols=100)
    app = build_test_app(settings, agent_answer="x", quote_feed=_feed(fetcher, registry))
    url = "/api/v1/quote/stream?symbols=2330&market=TW"

    # Two concurrent subscribers to the same symbol on the same worker.
    first, second = await asyncio.gather(
        collect_sse(app, url, until=lambda ls: bool(quote_frames(ls))),
        collect_sse(app, url, until=lambda ls: bool(quote_frames(ls))),
    )

    for lines in (first, second):
        frames = quote_frames(lines)
        assert frames, "each subscriber must receive a quote frame"
        assert frames[0]["symbol"] == "2330"
        assert frames[0]["as_of"] == "2024-01-02"  # freshness metadata present
        assert frames[0]["market_open"] is True
    # Exactly ONE upstream fetch served both — the shared-poll value proposition. (Both connections
    # register before the warm-up sleep elapses, so the first poll fans out to both.)
    assert fetcher.calls.count(("TW", "2330")) == 1


async def test_disconnect_drops_demand_refcount(settings: Settings) -> None:
    fetcher = _CountingFetcher()
    registry = InMemoryDemandRegistry(max_symbols=100)
    app = build_test_app(settings, agent_answer="x", quote_feed=_feed(fetcher, registry))
    url = "/api/v1/quote/stream?symbols=2330&market=TW"

    # A live subscription holds a demand refcount...
    lines = await collect_sse(app, url, until=lambda ls: bool(quote_frames(ls)))
    assert quote_frames(lines), "subscription was live (received a quote)"

    # ...and `collect_sse` has now disconnected, so the route's finally-block decremented the
    # refcount to zero — the symbol left the demand set (poller stops touching an abandoned symbol).
    for _ in range(100):
        if ("TW", "2330") not in registry._counts:
            break
        await asyncio.sleep(0.02)
    assert ("TW", "2330") not in registry._counts


async def test_updates_stream_relays_data_updated_from_real_ingest(
    settings: Settings, tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: run the REAL `job_price_eod` batch-completion path with the app's own broker as
    its publisher, and confirm `/updates/stream` relays the resulting frame — not a standalone
    `publish_data_updated()` call."""
    broker = InMemoryDataUpdateBroker()
    app = build_test_app(
        settings,
        agent_answer="x",
        data_update_broker=DataUpdateBroker(publisher=broker, reader_factory=broker),
    )

    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    ingest_ctx = IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=broker,  # the SAME broker the app's /updates/stream reads from
    )

    async def _run_real_ingest() -> None:
        # Give the SSE subscription time to land on the broker before the batch publishes.
        await asyncio.sleep(0.2)
        await job_price_eod(date(2024, 1, 2), ingest_ctx)

    lines = await collect_sse(
        app,
        "/api/v1/updates/stream",
        until=lambda ls: bool(frames_of_kind(ls, "data.updated")),
        on_started=_run_real_ingest,
        timeout_s=6.0,
    )

    frames = frames_of_kind(lines, "data.updated")
    assert frames, "the ingest batch-completion signal was relayed to the SSE client"
    assert frames[0]["capability"] == "price_eod"
    assert frames[0]["market"] == "TW"
    assert frames[0]["scope"] == "whole-market"
    assert frames[0]["as_of"] == "2024-01-02"


async def test_anonymous_caller_gets_masked_teaser_when_quote_is_sponsor(
    settings: Settings,
) -> None:
    """When the deployed `get_price_quote` is sponsor-classed, an unentitled (anonymous → Free)
    caller's stream frames carry the masked teaser, not real data — the same gate the one-shot
    `/quote` applies, re-applied per connection over the shared poll (never a hard error)."""
    fetcher = _CountingFetcher()
    registry = InMemoryDemandRegistry(max_symbols=100)
    app = build_test_app(
        settings,
        agent_answer="x",
        tool_registry=build_sponsor_registry(),
        quote_feed=_feed(fetcher, registry),
    )
    url = "/api/v1/quote/stream?symbols=2330&market=TW"

    lines = await collect_sse(app, url, until=lambda ls: bool(quote_frames(ls)))
    frames = quote_frames(lines)
    assert frames, "a frame was still delivered (masked, never a hard error)"
    quote = frames[0]["quote"]
    assert isinstance(quote, dict)
    assert quote.get("locked") is True
    assert quote.get("required_tier") == "premium"
