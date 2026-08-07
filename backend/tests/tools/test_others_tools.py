from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import DateRangeInput, MarketOnlyInput
from tools.others_tools import GetActiveEtfRosterTool, GetFinmindStockNewsTool


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_active_etf_roster_reads_info_key() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "symbol": "00981A",
        "market": "TW",
        "as_of": "2026-08-01",
        "name": "Some ETF",
        "category": "domestic",
        "fund_type": "twse",
    }
    router, _ = make_router(symbol, {Capability.ACTIVE_ETF_INFO: _result({"info": [row]})})
    tool = GetActiveEtfRosterTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.rows[0].name == "Some ETF"


async def test_get_finmind_stock_news_delegates() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "description": "desc",
        "link": "http://example.com",
        "source": "econ daily",
        "title": "title",
    }
    router, adapter = make_router(symbol, {Capability.STOCK_NEWS: _result({"rows": [row]})})
    tool = GetFinmindStockNewsTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.STOCK_NEWS
    assert output.rows[0].title == "title"
