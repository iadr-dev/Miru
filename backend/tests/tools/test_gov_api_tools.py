from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import SymbolInput
from tools.gov_api_tools import GetDerivativesSettlementTool


async def test_get_derivatives_settlement_delegates_and_parses() -> None:
    symbol = Symbol(id="TXFA", market=Market.TW)
    record = {
        "symbol": "TXFA",
        "market": "TW",
        "trade_date": "2026-08-01",
        "contract_month": "202608",
        "settlement_price": 20000.0,
        "volume": 1000,
        "open_interest": 5000,
    }
    result = SourceResult(
        source_id="TAIFEX_OPENAPI",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"record": record},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.DERIVATIVES_OI: result})
    tool = GetDerivativesSettlementTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="TXFA", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.DERIVATIVES_OI
    assert output.record is not None
    assert output.record.settlement_price == 20000.0
