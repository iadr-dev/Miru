"""Tests for FinMind Derivative-category dataset mappers — recorded fixtures, no live
network."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_futopt_info_is_reference_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "futopt_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.FUTOPT_INFO, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["code"] for row in rows} == {"TX", "TXO"}  # type: ignore[index]


async def test_fetch_futures_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "futures_daily_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.FUTURES_DAILY, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["settlement_price"] == 10550.0  # type: ignore[index]


async def test_fetch_option_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "option_daily_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(Capability.OPTION_DAILY, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["call_put"] == "call"  # type: ignore[index]


async def test_fetch_futures_tick_trading_day_attribution_and_double_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tick volume is double-sided and the after-hours session is attributed to the next
    trading day — this mapper passes both quirks through unchanged, never re-buckets `date`."""
    body = load_fixture("finmind", "futures_tick_mtx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="MTX", market=Market.TW)

    result = await adapter.fetch(Capability.FUTURES_TICK, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    ticks = result.payload["ticks"]
    assert isinstance(ticks, list)
    assert ticks[0]["trade_date"] == "2020-04-01"  # type: ignore[index]
    assert ticks[1]["trade_date"] == "2020-04-02"  # type: ignore[index]


async def test_fetch_futures_spread_tick_two_leg_contract_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`contract_date` carries both legs of the calendar spread; the spread price can be
    negative."""
    body = load_fixture("finmind", "futures_spread_tick_caf.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="CAF", market=Market.TW)

    result = await adapter.fetch(Capability.FUTURES_SPREAD_TICK, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    ticks = result.payload["ticks"]
    assert isinstance(ticks, list)
    assert ticks[0]["contract_date"] == "202606/202607"  # type: ignore[index]
    assert ticks[0]["price"] == -5.0  # type: ignore[index]
    assert ticks[0]["spread_to_spread"] == 1  # type: ignore[index]


async def test_fetch_option_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "option_tick_oco.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="OCO", market=Market.TW)

    result = await adapter.fetch(Capability.OPTION_TICK, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    ticks = result.payload["ticks"]
    assert isinstance(ticks, list)
    assert ticks[0]["put_call"] == "call"  # type: ignore[index]


async def test_fetch_futures_institutional_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "futures_institutional_flow_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(
        Capability.FUTURES_INSTITUTIONAL_FLOW, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["investor"] == "Foreign_Investor"  # type: ignore[index]
    assert rows[0]["symbol"] == "TX"  # type: ignore[index]


async def test_fetch_option_institutional_flow_split_by_call_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "option_institutional_flow_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(
        Capability.OPTION_INSTITUTIONAL_FLOW, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["call_put"] for row in rows} == {"call", "put"}  # type: ignore[index]


async def test_fetch_futures_institutional_flow_after_hours_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "futures_institutional_flow_after_hours_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(
        Capability.FUTURES_INSTITUTIONAL_FLOW_AFTER_HOURS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["long_deal_volume"] == 200  # type: ignore[index]


async def test_fetch_option_institutional_flow_after_hours_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "option_institutional_flow_after_hours_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(
        Capability.OPTION_INSTITUTIONAL_FLOW_AFTER_HOURS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["call_put"] == "call"  # type: ignore[index]


async def test_fetch_futures_dealer_volume_after_hour_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "futures_dealer_volume_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.FUTURES_DEALER_VOLUME, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["is_after_hour"] is False  # type: ignore[index]
    assert rows[1]["is_after_hour"] is True  # type: ignore[index]


async def test_fetch_option_dealer_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "option_dealer_volume_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(Capability.OPTION_DEALER_VOLUME, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["dealer_name"] == "元大"  # type: ignore[index]


async def test_fetch_futures_oi_large_traders_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "futures_oi_large_traders_tjf.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TJF", market=Market.TW)

    result = await adapter.fetch(
        Capability.FUTURES_OI_LARGE_TRADERS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market_open_interest"] == 20000  # type: ignore[index]


async def test_fetch_option_oi_large_traders_put_call_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`TaiwanOptionOpenInterestLargeTraders`'s put/call open-interest-concentration split."""
    body = load_fixture("finmind", "option_oi_large_traders_ca.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="CA", market=Market.TW)

    result = await adapter.fetch(
        Capability.OPTION_OI_LARGE_TRADERS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["put_call"] for row in rows} == {"call", "put"}  # type: ignore[index]
    call_row = next(r for r in rows if r["put_call"] == "call")  # type: ignore[index]
    put_row = next(r for r in rows if r["put_call"] == "put")  # type: ignore[index]
    assert call_row["market_open_interest"] != put_row["market_open_interest"]  # type: ignore[index]


async def test_fetch_futures_spread_trading_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "futures_spread_trading_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(
        Capability.FUTURES_SPREAD_TRADING, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["best_bid"] == -9.0  # type: ignore[index]


async def test_fetch_futures_final_settlement_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "futures_final_settlement_price_tx.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(
        Capability.FUTURES_FINAL_SETTLEMENT_PRICE, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["instrument_name"] == "臺股期貨"  # type: ignore[index]


async def test_fetch_option_final_settlement_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "option_final_settlement_price_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(
        Capability.OPTION_FINAL_SETTLEMENT_PRICE, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["instrument_type"] == "臺指選擇權"  # type: ignore[index]


async def test_fetch_option_vix_is_market_level(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "option_vix.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(Capability.OPTION_VIX, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["vix"] == 18.5  # type: ignore[index]


async def test_fetch_asset_swap_fixed_income_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "asset_swap_fixed_income_daily_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.ASSET_SWAP_FIXED_INCOME_DAILY, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["rate_average"] == 1.35  # type: ignore[index]


async def test_fetch_asset_swap_option_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "asset_swap_option_daily_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.ASSET_SWAP_OPTION_DAILY, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["premium_average"] == 3.6  # type: ignore[index]
