from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.chip_tools import (
    GetGovernmentBankFlowsTool,
    GetInstitutionalFlowTotalMarketTool,
    GetMarginShortTool,
    GetSecuritiesTraderInfoTool,
)
from tools.common import DateRangeInput, MarketOnlyInput


def _result(
    payload: dict[str, object], *, data_class: DataClass = DataClass.PUBLIC
) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=data_class,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_margin_short_delegates_and_parses_rows() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "margin_purchase_buy": 1,
        "margin_purchase_cash_repayment": 0,
        "margin_purchase_limit": 0,
        "margin_purchase_sell": 0,
        "margin_purchase_today_balance": 0,
        "margin_purchase_yesterday_balance": 0,
        "note": None,
        "offset_loan_and_short": 0,
        "short_sale_buy": 0,
        "short_sale_cash_repayment": 0,
        "short_sale_limit": 0,
        "short_sale_sell": 0,
        "short_sale_today_balance": 0,
        "short_sale_yesterday_balance": 0,
    }
    router, adapter = make_router(symbol, {Capability.MARGIN_SHORT: _result({"rows": [row]})})
    tool = GetMarginShortTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.MARGIN_SHORT
    assert output.rows[0].margin_purchase_buy == 1


async def test_get_institutional_flow_total_market_is_market_only_input() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trade_date": "2026-08-01",
        "name": "Foreign_Investor",
        "buy": 1,
        "sell": 2,
    }
    router, adapter = make_router(
        symbol, {Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET: _result({"rows": [row]})}
    )
    tool = GetInstitutionalFlowTotalMarketTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET
    assert output.rows[0].name == "Foreign_Investor"


async def test_get_securities_trader_info_reads_market_level_roster() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trader_id": "1020",
        "trader_name": "Fubon",
        "as_of": "2026-08-01",
        "address": None,
        "phone": None,
    }
    router, _ = make_router(symbol, {Capability.SECURITIES_TRADER_INFO: _result({"rows": [row]})})
    tool = GetSecuritiesTraderInfoTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.rows[0].trader_name == "Fubon"


async def test_get_government_bank_flows_is_sponsor_gated() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "buy_amount": 1.0,
        "sell_amount": 0.0,
        "buy": 1,
        "sell": 0,
        "bank_name": "Bank A",
    }
    router, _ = make_router(
        symbol,
        {
            Capability.GOVERNMENT_BANK_BUYSELL: _result(
                {"rows": [row]}, data_class=DataClass.SPONSOR
            )
        },
    )
    tool = GetGovernmentBankFlowsTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = DateRangeInput(id="2330", market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)

    unmasked = await dispatch_tool(tool, args, ToolContext(tier=Tier.PREMIUM))
    assert not isinstance(unmasked, MaskedTeaser)
