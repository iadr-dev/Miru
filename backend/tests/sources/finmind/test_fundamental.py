"""Tests for FinMind Fundamental-category dataset mappers — recorded fixtures, no live
network."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_dividends(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "dividends_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.DIVIDENDS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    events = result.payload["events"]
    assert isinstance(events, list)
    assert events[0]["cash_dividend"] == 11.0  # type: ignore[index]


async def test_fetch_financials(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "financials_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    lines = result.payload["lines"]
    assert isinstance(lines, list)
    assert {line["item"] for line in lines} == {"EPS", "Revenue"}  # type: ignore[index]


async def test_fetch_balance_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "balance_sheet_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.BALANCE_SHEET, sym, tls_policy=TLSPolicy.STRICT)

    lines = result.payload["lines"]
    assert isinstance(lines, list)
    assert {line["statement_type"] for line in lines} == {"balance_sheet"}  # type: ignore[index]
    assert {line["item"] for line in lines} == {"TotalAssets", "TotalLiabilities"}  # type: ignore[index]


async def test_fetch_cash_flow_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cash_flow_statement_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.CASH_FLOW_STATEMENT, sym, tls_policy=TLSPolicy.STRICT)

    lines = result.payload["lines"]
    assert isinstance(lines, list)
    assert lines[0]["statement_type"] == "cash_flow"  # type: ignore[index]


async def test_fetch_dividend_result(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "dividend_result_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.DIVIDEND_RESULT, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["stock_and_cache_dividend"] == 10.5  # type: ignore[index]


async def test_fetch_month_revenue_empty_create_time_for_old_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older historical rows have an empty `create_time` — must map to `None`, not `""`."""
    body = load_fixture("finmind", "month_revenue_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.MONTH_REVENUE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["create_time"] == "2019-04-10"  # type: ignore[index]
    assert rows[1]["create_time"] is None  # type: ignore[index]


async def test_fetch_capital_reduction_reference_price_is_not_whole_market_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`data_id` is documented as optional but the omit-data_id whole-range form requires
    Backer/Sponsor — free-tier callers must always pass `data_id`, so this capability is not
    marked whole_market_ok."""
    body = load_fixture("finmind", "capital_reduction_reference_price_2327.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2327", market=Market.TW)

    result = await adapter.fetch(
        Capability.CAPITAL_REDUCTION_REFERENCE_PRICE, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["reason_for_capital_reduction"] == "彌補虧損"  # type: ignore[index]

    whole = await adapter.fetch_whole_market(
        Capability.CAPITAL_REDUCTION_REFERENCE_PRICE, tls_policy=TLSPolicy.STRICT
    )
    assert whole.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_market_value_is_sponsor_only_no_free_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "market_value_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.MARKET_VALUE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market_value"] == 14000000000000  # type: ignore[index]


async def test_fetch_stock_delisting_is_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "stock_delisting.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="1234", market=Market.TW)

    result = await adapter.fetch(Capability.STOCK_DELISTING, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["stock_name"] == "某某公司"  # type: ignore[index]


async def test_fetch_market_value_weight_is_sponsor_only_no_free_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "market_value_weight_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.MARKET_VALUE_WEIGHT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["rank"] == 1  # type: ignore[index]
    assert rows[0]["listing_type"] == "twse"  # type: ignore[index]


async def test_fetch_split_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "split_price.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="6415", market=Market.TW)

    result = await adapter.fetch(Capability.SPLIT_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["split_type"] == "分割"  # type: ignore[index]


async def test_fetch_par_value_change(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "par_value_change.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2911", market=Market.TW)

    result = await adapter.fetch(Capability.PAR_VALUE_CHANGE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["after_ref_close"] == 20.0  # type: ignore[index]
