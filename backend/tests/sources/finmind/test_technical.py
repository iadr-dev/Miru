"""Tests for FinMind Technical-category dataset mappers — recorded fixtures, no live network."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_price_eod_filters_zero_price_days(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "price_eod_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.PRICE_EOD,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2024-01-01",
        end_date="2024-01-05",
    )

    assert result.source_id == "FINMIND"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    bars = result.payload["bars"]
    assert isinstance(bars, list)
    # The 2024-01-03 zero-price no-trade day must be filtered out.
    assert len(bars) == 2
    assert all(bar["close"] > 0 for bar in bars)  # type: ignore[index]


async def test_fetch_stock_info_is_public_reference_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "stock_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["symbol"] for row in rows} == {"2330", "0050"}  # type: ignore[index]


async def test_fetch_stock_info_with_warrant(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "stock_info_with_warrant.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="084655", market=Market.TW)

    result = await adapter.fetch(
        Capability.STOCK_INFO_WITH_WARRANT, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2


async def test_fetch_warrant_underlying_multi_row_per_recycled_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warrant code can be recycled to a different underlying across issues — both rows
    must survive the mapper untouched."""
    body = load_fixture("finmind", "warrant_underlying_084655.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="084655", market=Market.TW)

    result = await adapter.fetch(Capability.WARRANT_UNDERLYING, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["target_stock_id"] == "2330"  # type: ignore[index]
    assert rows[1]["target_stock_id"] == "2454"  # type: ignore[index]


async def test_fetch_trading_calendar_is_market_level(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "trading_calendar.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TRADING_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 3
    assert all(row["market"] == "TW" for row in rows)  # type: ignore[index]


async def test_fetch_price_eod_adjusted_does_not_filter_zero_volume_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike TaiwanStockPrice, PriceAdj carries the previous day's price forward instead of
    publishing 0 — the no-trade day must NOT be filtered out."""
    body = load_fixture("finmind", "price_eod_adjusted_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2317", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD_ADJUSTED, sym, tls_policy=TLSPolicy.STRICT)

    bars = result.payload["bars"]
    assert isinstance(bars, list)
    assert len(bars) == 2
    assert bars[1]["close"] == 166.54  # type: ignore[index]
    assert bars[1]["volume"] == 0  # type: ignore[index]


async def test_fetch_price_tick_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "price_tick_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_TICK, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    ticks = result.payload["ticks"]
    assert isinstance(ticks, list)
    assert ticks[0]["tick_type"] == "1"  # type: ignore[index]
    assert ticks[1]["tick_type"] == "2"  # type: ignore[index]


async def test_fetch_valuation_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "valuation_ratios_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.VALUATION_RATIOS, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["per"] == 19.5  # type: ignore[index]


async def test_fetch_order_book_stats_is_market_level(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "order_book_stats.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ORDER_BOOK_STATS, sym, tls_policy=TLSPolicy.STRICT)

    stats = result.payload["stats"]
    assert isinstance(stats, list)
    assert stats[0]["total_deal_volume"] == 2500  # type: ignore[index]


async def test_fetch_taiex_5s_index(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "taiex_5s_index.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TAIEX_5S_INDEX, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2


async def test_fetch_day_trading_buy_after_sale_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "day_trading_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.DAY_TRADING, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[1]["buy_after_sale"] == "*"  # type: ignore[index]


async def test_fetch_total_return_index_uses_pseudo_code_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "total_return_index.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TAIEX", market=Market.TW)

    result = await adapter.fetch(Capability.TOTAL_RETURN_INDEX, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "TAIEX"  # type: ignore[index]


async def test_fetch_ten_year_price_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "ten_year_price_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TEN_YEAR_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[1]["close"] == 305.5  # type: ignore[index]


async def test_fetch_intraday_kbar_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "kbar_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.INTRADAY_KBAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    bars = result.payload["bars"]
    assert isinstance(bars, list)
    assert bars[0]["minute"] == "09:00"  # type: ignore[index]


async def test_fetch_price_weekly(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "price_weekly_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_WEEKLY, sym, tls_policy=TLSPolicy.STRICT)

    bars = result.payload["bars"]
    assert isinstance(bars, list)
    assert bars[0]["period"] == "2020-14"  # type: ignore[index]


async def test_fetch_price_monthly(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "price_monthly_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_MONTHLY, sym, tls_policy=TLSPolicy.STRICT)

    bars = result.payload["bars"]
    assert isinstance(bars, list)
    assert bars[0]["period"] == "2020-04"  # type: ignore[index]


async def test_fetch_index_5s_snapshot_reads_symbol_from_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `data_id` filter is documented for this dataset — every row's own `stock_id` must be
    used even on a per-symbol call."""
    body = load_fixture("finmind", "index_5s_snapshot.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TAIEX", market=Market.TW)

    result = await adapter.fetch(Capability.INDEX_5S_SNAPSHOT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["symbol"] for row in rows} == {"TAIEX", "OTC"}  # type: ignore[index]


async def test_fetch_trading_suspension_is_event_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "trading_suspension.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2618", market=Market.TW)

    result = await adapter.fetch(Capability.TRADING_SUSPENSION, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["resumption_date"] == "2024-12-06"  # type: ignore[index]


async def test_fetch_day_trading_suspension(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "day_trading_suspension.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="6477", market=Market.TW)

    result = await adapter.fetch(
        Capability.DAY_TRADING_SUSPENSION, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["reason"] == "異常交易處置"  # type: ignore[index]


async def test_fetch_price_limit_zero_means_no_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "price_limit_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_LIMIT, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[1]["limit_up"] == 0.0  # type: ignore[index]
    assert rows[1]["limit_down"] == 0.0  # type: ignore[index]
