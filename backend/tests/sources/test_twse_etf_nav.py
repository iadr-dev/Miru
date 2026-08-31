"""TWSE MIS ETF NAV/premium-discount adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.twse_etf_nav as twse_etf_nav_module
from schemas.enums import DataClass, TLSPolicy
from schemas.source import HealthStatus
from sources.twse_etf_nav import fetch_whole_market_etf_nav
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(twse_etf_nav_module, "build_async_client", make_json_client_factory(body))


async def test_fetch_parses_every_group_into_flat_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_etf_nav", "all_etf_sample.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TWSE_MIS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 3  # one per issuer group in the fixture, flattened
    symbols = {row["symbol"] for row in rows}
    assert symbols == {"0050", "00631L", "00987A"}


async def test_field_mapping_matches_twse_own_published_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed live against the page's own rendered column headers AND by cross-checking
    0050's `e` value against real ingested `TaiwanStockPrice` close data for the same date —
    `e`=close price, `f`=estimated NAV, `g`=TWSE's own published premium/discount %,
    `h`=prior-day NAV. Getting this mapping backward would silently swap price and NAV."""
    body = load_fixture("twse_etf_nav", "all_etf_sample.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    row_0050 = next(r for r in rows if r["symbol"] == "0050")
    assert row_0050["close_price"] == 106.95
    assert row_0050["estimated_nav"] == 107.21
    assert row_0050["premium_discount_pct"] == -0.24
    assert row_0050["prior_day_nav"] == 106.14
    assert row_0050["as_of"] == "2026-08-28"


async def test_handles_mixed_string_and_numeric_field_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upstream feed inconsistently types the same field as a JSON number in one issuer
    group and a string in another (a real, confirmed-live upstream quirk) — both must parse to
    the same numeric value."""
    body = load_fixture("twse_etf_nav", "all_etf_sample.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    row_0050 = next(r for r in rows if r["symbol"] == "0050")  # numeric-typed fields in fixture
    row_631l = next(r for r in rows if r["symbol"] == "00631L")  # string-typed fields in fixture
    assert isinstance(row_0050["close_price"], float)
    assert isinstance(row_631l["close_price"], float)
    assert row_631l["close_price"] == 36.30


async def test_missing_a1_key_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, {"unexpected": "shape"})

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload["rows"] == []


async def test_empty_groups_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, {"a1": []})

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload["rows"] == []


async def test_transport_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_etf_nav_module, "build_async_client", factory)

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload["rows"] == []


async def test_fetch_is_single_shot_never_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """One call must issue exactly one upstream HTTP request — no internal loop, matching the
    whole-market single-call convention every other ingest-layer fetch function follows."""
    call_count = 0
    body = load_fixture("twse_etf_nav", "all_etf_sample.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_etf_nav_module, "build_async_client", factory)

    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)

    assert call_count == 1
    assert result.health.status is HealthStatus.OK
