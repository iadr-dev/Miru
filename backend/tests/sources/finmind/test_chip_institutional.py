"""Tests for FinMind Chip/Institutional-category dataset mappers — recorded fixtures, no live
network."""

from __future__ import annotations

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_institutional_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "institutional_flow_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.INSTITUTIONAL_FLOW, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2


async def test_fetch_chip_broker_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "chip_broker_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.CHIP_BROKER, sym, tls_policy=TLSPolicy.STRICT, date="2024-01-02"
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["broker_name"] == "元大"  # type: ignore[index]


async def test_fetch_margin_short(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "margin_short_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.MARGIN_SHORT, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["short_sale_today_balance"] == 5000  # type: ignore[index]


async def test_fetch_margin_short_total_market_is_market_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "margin_short_total_market.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.MARGIN_SHORT_TOTAL_MARKET, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["name"] == "MarginPurchaseMoney"  # type: ignore[index]


async def test_fetch_institutional_flow_wide_historical_zero_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dealer_self/Dealer_Hedging/Foreign_Dealer_Self read 0 in the pre-2014-12-01 era when
    they did not yet exist as separate categories."""
    body = load_fixture("finmind", "institutional_flow_wide_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.INSTITUTIONAL_FLOW_WIDE, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[1]["dealer_self_buy"] == 0  # type: ignore[index]
    assert rows[1]["dealer_buy"] == 500  # type: ignore[index]
    assert rows[0]["dealer_buy"] == 0  # type: ignore[index]
    assert rows[0]["dealer_self_buy"] == 150  # type: ignore[index]


async def test_fetch_institutional_flow_total_market(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "institutional_flow_total_market.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["name"] == "Foreign_Investor"  # type: ignore[index]


async def test_fetch_foreign_shareholding(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "foreign_shareholding_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FOREIGN_SHAREHOLDING, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["foreign_investment_shares_ratio"] == 79.5  # type: ignore[index]


async def test_fetch_shareholding_distribution_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "shareholding_distribution_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.SHAREHOLDING_DISTRIBUTION, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["holding_shares_level"] == "1-999"  # type: ignore[index]


async def test_fetch_securities_lending(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "securities_lending_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.SECURITIES_LENDING, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["transaction_type"] == "競價"  # type: ignore[index]


async def test_fetch_margin_short_sale_suspension(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "margin_short_sale_suspension_0050.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(
        Capability.MARGIN_SHORT_SALE_SUSPENSION, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["reason"] == "除息"  # type: ignore[index]


async def test_fetch_daily_short_sale_balances(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "daily_short_sale_balances_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.DAILY_SHORT_SALE_BALANCES, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["sbl_short_sales_current_day_balance"] == 2100  # type: ignore[index]


async def test_fetch_securities_trader_info_is_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "securities_trader_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="9200", market=Market.TW)

    result = await adapter.fetch(
        Capability.SECURITIES_TRADER_INFO, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["trader_name"] == "元大"  # type: ignore[index]


async def test_fetch_warrant_chip_broker_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "warrant_chip_broker_084655.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="084655", market=Market.TW)

    result = await adapter.fetch(Capability.WARRANT_CHIP_BROKER, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["broker_name"] == "元大"  # type: ignore[index]


async def test_fetch_government_bank_buysell_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "government_bank_buysell.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.GOVERNMENT_BANK_BUYSELL, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["bank_name"] == "台灣銀行"  # type: ignore[index]


async def test_fetch_margin_maintenance_ratio_is_market_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "margin_maintenance_ratio.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.MARGIN_MAINTENANCE_RATIO, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["total_exchange_margin_maintenance"] == 165.32  # type: ignore[index]


async def test_fetch_chip_broker_agg_uses_dedicated_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "chip_broker_agg_2330.json")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.CHIP_BROKER_AGG,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2024-07-01",
        end_date="2024-07-15",
    )

    assert result.data_class is DataClass.SPONSOR
    assert "taiwan_stock_trading_daily_report_secid_agg" in str(captured["url"])
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["buy_volume"] == 100000  # type: ignore[index]


async def test_fetch_block_trade_broker_report_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "block_trade_broker_report.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.BLOCK_TRADE_BROKER_REPORT, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["trade_type"] == "普通"  # type: ignore[index]


async def test_fetch_block_trade_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "block_trade_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.BLOCK_TRADE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["volume"] == 50000  # type: ignore[index]


async def test_fetch_loan_collateral_balance_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "loan_collateral_balance_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.LOAN_COLLATERAL_BALANCE, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["exchange_market"] == "TWSE"  # type: ignore[index]
    assert rows[0]["settlement_margin_next_day_quota"] == 40000  # type: ignore[index]


async def test_fetch_industry_chain_money_flow_total_vs_sub_industry_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty `sub_industry` marks an industry-chain total row, which does NOT equal the
    sum of its sub-industry rows."""
    body = load_fixture("finmind", "industry_chain_money_flow.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.INDUSTRY_CHAIN_MONEY_FLOW, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    total_row = next(r for r in rows if r["sub_industry"] == "")  # type: ignore[index]
    sub_row = next(r for r in rows if r["sub_industry"] == "IC設計")  # type: ignore[index]
    assert total_row["trading_money_pct"] != sub_row["trading_money_pct"]  # type: ignore[index]


async def test_fetch_disposition_securities(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "disposition_securities_6477.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="6477", market=Market.TW)

    result = await adapter.fetch(
        Capability.DISPOSITION_SECURITIES, sym, tls_policy=TLSPolicy.STRICT
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["measure"] == "分盤交易"  # type: ignore[index]
