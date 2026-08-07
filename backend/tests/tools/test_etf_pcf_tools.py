from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import SymbolInput
from tools.etf_pcf_tools import GetEtfFundInfoTool


async def test_get_etf_fund_info_delegates_and_parses() -> None:
    symbol = Symbol(id="0050", market=Market.TW)
    info = {
        "symbol": "0050",
        "market": "TW",
        "fund_name": "元大台灣50",
        "fund_type": "被動式",
        "tracked_index": "台灣50指數",
        "units_outstanding": 1000,
        "listed_date": "2003-06-30",
        "manager_company": "元大投信",
    }
    result = SourceResult(
        source_id="ETF_PCF",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"info": info},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.ETF_HOLDINGS: result})
    tool = GetEtfFundInfoTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="0050", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.ETF_HOLDINGS
    assert output.info is not None
    assert output.info.fund_name == "元大台灣50"
