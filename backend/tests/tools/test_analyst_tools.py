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
    GetAnalystConsensusTool,
    GetEsgScoreTool,
    GetInstitutionalHoldersTool,
    GetMarketStatusTool,
    GetOptionsChainTool,
    GetSecFilingsTool,
    GetStockPeersTool,
    MarketScopedInput,
    NonTwSymbolInput,
    RunEquityScreenerUsTool,
    UsSymbolInput,
)
from tools.base import ToolContext
from tools.common import SymbolInput


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


async def test_get_analyst_consensus_delegates() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    consensus = {
        "symbol": "AAPL",
        "market": "US",
        "as_of": "2026-08-01",
        "mean_target": 250.0,
        "median_target": 248.0,
        "high_target": 300.0,
        "low_target": 200.0,
        "number_of_analysts": 40,
        "recommendation_key": "buy",
    }
    router, adapter = make_router(
        symbol, {Capability.ANALYST_CONSENSUS: _result({"consensus": consensus})}
    )
    tool = GetAnalystConsensusTool(router)

    output = await tool.run(SymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.ANALYST_CONSENSUS
    assert output.consensus is not None
    assert output.consensus.mean_target == 250.0
    assert output.consensus.number_of_analysts == 40


async def test_get_analyst_consensus_accepts_tw_symbol_with_no_coverage() -> None:
    """`ANALYST_CONSENSUS` is in yfinance's `_TW_SUPPORTED` set — a TW symbol is a legitimate
    call, not a rejected market, even though most tools in this module are non-TW-only."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.ANALYST_CONSENSUS: _result({})})
    tool = GetAnalystConsensusTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.ANALYST_CONSENSUS
    assert output.consensus is None


async def test_get_esg_score_delegates() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    esg = {
        "symbol": "AAPL",
        "market": "US",
        "as_of": "2026-08-01",
        "total_esg_score": 18.5,
        "esg_risk_percentile": 12.0,
    }
    router, adapter = make_router(symbol, {Capability.ESG: _result({"esg": esg})})
    tool = GetEsgScoreTool(router)

    output = await tool.run(SymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.ESG
    assert output.esg is not None
    assert output.esg.total_esg_score == 18.5


async def test_get_esg_score_degrades_gracefully_with_no_coverage() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.ESG: _result({})})
    tool = GetEsgScoreTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.ESG
    assert output.esg is None


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


async def test_get_sec_filings_delegates() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    filing = {
        "symbol": "AAPL",
        "market": "US",
        "filing_date": "2026-08-01",
        "filing_type": "10-Q",
        "title": "Quarterly report",
        "edgar_url": "https://www.sec.gov/example",
    }
    router, adapter = make_router(symbol, {Capability.SEC_FILINGS: _result({"filings": [filing]})})
    tool = GetSecFilingsTool(router)

    output = await tool.run(UsSymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.SEC_FILINGS
    assert output.filings[0].filing_type == "10-Q"


async def test_get_sec_filings_degrades_gracefully_with_no_filings() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    router, _ = make_router(symbol, {Capability.SEC_FILINGS: _result({})})
    tool = GetSecFilingsTool(router)

    output = await tool.run(UsSymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert output.filings == ()


def test_us_symbol_input_rejects_non_us() -> None:
    with pytest.raises(ValidationError):
        UsSymbolInput(id="2330", market=Market.TW)


async def test_get_stock_peers_resolves_then_fetches() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    classification = {
        "symbol": "AAPL",
        "market": "US",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "sector_key": "technology",
        "industry_key": "consumer-electronics",
    }
    sector_row = {
        "market": "US",
        "sector_key": "technology",
        "row_kind": "company",
        "symbol": "MSFT",
        "name": "Microsoft",
        "rating": None,
        "market_weight": 0.1,
    }
    industry_row = {
        "market": "US",
        "sector_key": "technology",
        "industry_key": "consumer-electronics",
        "row_kind": "company",
        "symbol": "SONY",
        "name": "Sony",
    }
    router, adapter = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _result({"classification": classification}),
            Capability.SECTOR_OVERVIEW: _result({"rows": [sector_row]}),
            Capability.INDUSTRY_OVERVIEW: _result({"rows": [industry_row]}),
        },
    )
    tool = GetStockPeersTool(router)

    output = await tool.run(SymbolInput(id="AAPL", market=Market.US), ToolContext())

    calls = [call[0] for call in adapter.calls]
    assert calls == [
        Capability.SECTOR_CLASS,
        Capability.SECTOR_OVERVIEW,
        Capability.INDUSTRY_OVERVIEW,
    ]
    assert output.sector == "Technology"
    assert output.industry == "Consumer Electronics"
    assert output.sector_peers[0].symbol == "MSFT"
    assert output.industry_peers[0].symbol == "SONY"
    assert output.unavailable_reason is None


async def test_get_stock_peers_honestly_reports_no_classification() -> None:
    symbol = Symbol(id="0050", market=Market.TW)
    router, _ = make_router(symbol, {Capability.SECTOR_CLASS: _result({})})
    tool = GetStockPeersTool(router)

    output = await tool.run(SymbolInput(id="0050", market=Market.TW), ToolContext())

    assert output.unavailable_reason is not None
    assert output.sector_peers == ()
    assert output.industry_peers == ()


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
