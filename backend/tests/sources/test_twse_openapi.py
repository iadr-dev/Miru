"""TWSE OpenAPI adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.twse_openapi as twse_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_openapi import (
    TwseOpenApiAdapter,
    _split_slash_roc_date_range,
    fetch_whole_market_attention_stocks,
    fetch_whole_market_disposition_events,
)
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(twse_module, "build_async_client", make_json_client_factory(body))


def test_supports_price_eod_tw_only() -> None:
    adapter = TwseOpenApiAdapter()
    assert adapter.supports() == {(Capability.PRICE_EOD, Market.TW)}


def test_validate_rejects_non_list_rows() -> None:
    adapter = TwseOpenApiAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


async def test_fetch_filters_to_requested_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_openapi", "stock_day_all.json")
    _patch_client(monkeypatch, body)
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TWSE_OPENAPI"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    bar = result.payload["bar"]
    assert bar["symbol"] == "2330"  # type: ignore[index]
    assert bar["close"] == 593.0  # type: ignore[index]


async def test_fetch_symbol_not_in_dump_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_openapi", "stock_day_all.json")
    _patch_client(monkeypatch, body)
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


def test_split_slash_roc_date_range() -> None:
    start, end = _split_slash_roc_date_range("115/09/03～115/09/09")  # noqa: RUF001 — real upstream separator
    assert start is not None
    assert end is not None
    assert start.isoformat() == "2026-09-03"
    assert end.isoformat() == "2026-09-09"


def test_split_slash_roc_date_range_rejects_malformed_input() -> None:
    assert _split_slash_roc_date_range("not-a-range") == (None, None)
    assert _split_slash_roc_date_range(None) == (None, None)
    assert _split_slash_roc_date_range("115/09/03~115/09/09") == (None, None)  # ASCII tilde


async def test_fetch_whole_market_attention_stocks_drops_sentinel_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day with zero attention-stock flags returns one all-blank/all-zero sentinel row
    (confirmed live) — the fixture's real rows must survive, and a sentinel-style blank-`Code`
    row appended to them must be dropped, never mapped as a stock."""
    body = load_fixture("twse_openapi", "announcement_notice.json")
    sentinel = {
        "Number": "0",
        "Code": "",
        "Name": "",
        "NumberOfAnnouncement": "0",
        "TradingInfoForAttention": "",
        "Date": "",
        "ClosingPrice": "0",
        "PE": "0",
    }
    _patch_client(monkeypatch, [*body, sentinel])

    result = await fetch_whole_market_attention_stocks(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    assert by_symbol["3081"]["flagged_date"] == "2026-09-04"  # type: ignore[index]
    assert by_symbol["3081"]["security_type"] == "twse"  # type: ignore[index]
    assert by_symbol["30371"]["pe"] is None  # "N/A" -> None, not a fabricated value


async def test_fetch_whole_market_attention_stocks_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_module, "build_async_client", factory)

    result = await fetch_whole_market_attention_stocks(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {"rows": []}


async def test_fetch_whole_market_disposition_events_maps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("twse_openapi", "announcement_punish.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_disposition_events(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    row = by_symbol["3008"]
    assert row["period_start"] == "2026-09-03"  # type: ignore[index]
    assert row["period_end"] == "2026-09-09"  # type: ignore[index]
    assert row["measure"] == "第一次處置"  # type: ignore[index]
    assert row["security_type"] == "twse"  # type: ignore[index]


async def test_fetch_whole_market_disposition_events_drops_row_with_bad_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("twse_openapi", "announcement_punish.json")
    assert isinstance(body, list)
    broken_row = {**body[0], "DispositionPeriod": "not-a-range"}
    _patch_client(monkeypatch, [broken_row, body[1]])

    result = await fetch_whole_market_disposition_events(tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "3406"  # type: ignore[index]
