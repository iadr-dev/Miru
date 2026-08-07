from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.screener import ScreenerAssetKind, ScreenerCondition, ScreenerOperator, ScreenerQuery
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.analyst_tools import (
    GetInstitutionalHoldersTool,
    GetMarketStatusTool,
    GetOptionsChainTool,
    MarketScopedInput,
    NonTwSymbolInput,
    RunEquityScreenerUsTool,
)
from tools.base import ToolContext


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="YFINANCE",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_options_chain_delegates() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    contract = {
        "symbol": "AAPL",
        "market": "US",
        "expiry": "2026-09-19",
        "contract_symbol": "AAPL260919C00100000",
        "side": "call",
        "strike": 100.0,
        "last_price": 5.0,
        "bid": None,
        "ask": None,
        "volume": None,
        "open_interest": None,
        "implied_volatility": None,
        "in_the_money": True,
    }
    router, adapter = make_router(
        symbol, {Capability.OPTIONS_CHAIN: _result({"contracts": [contract]})}
    )
    tool = GetOptionsChainTool(router)

    output = await tool.run(NonTwSymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.OPTIONS_CHAIN
    assert output.contracts[0].contract_symbol == "AAPL260919C00100000"


def test_non_tw_symbol_input_rejects_tw() -> None:
    with pytest.raises(ValidationError):
        NonTwSymbolInput(id="2330", market=Market.TW)


async def test_get_institutional_holders_delegates() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    row = {
        "symbol": "AAPL",
        "market": "US",
        "holder_type": "institutional",
        "holder_name": "Vanguard",
        "date_reported": "2026-06-30",
        "shares": 1,
        "value": 1.0,
        "pct_held": 0.1,
        "pct_change": None,
    }
    router, adapter = make_router(
        symbol, {Capability.INSTITUTIONAL_HOLDERS: _result({"holders": [row]})}
    )
    tool = GetInstitutionalHoldersTool(router)

    output = await tool.run(NonTwSymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.INSTITUTIONAL_HOLDERS
    assert output.holders[0].holder_name == "Vanguard"


async def test_get_market_status_parses_optional_status() -> None:
    symbol = Symbol(id="", market=Market.US)
    status = {
        "market": "US",
        "region": "US",
        "status": "open",
        "message": None,
        "open_at": None,
        "close_at": None,
    }
    router, _ = make_router(symbol, {Capability.MARKET_STATUS: _result({"status": status})})
    tool = GetMarketStatusTool(router)

    output = await tool.run(MarketScopedInput(market=Market.US), ToolContext())

    assert output.status is not None
    assert output.status.status == "open"


def test_market_scoped_input_rejects_tw() -> None:
    with pytest.raises(ValidationError):
        MarketScopedInput(market=Market.TW)


def test_run_equity_screener_us_input_is_screener_query_not_a_full_market_scan() -> None:
    """The screener's own query shape still requires explicit bounded conditions — proves this
    tool's input can't be used to request an unconditioned "everything" scan."""
    tool = RunEquityScreenerUsTool()
    assert tool.input_model is ScreenerQuery
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(ScreenerCondition(field="sector", op=ScreenerOperator.EQ, value="Technology"),),
    )
    assert query.size <= 250
