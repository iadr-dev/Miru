from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import SymbolInput
from tools.live_tools import GetLiveQuoteSnapshotTool


async def test_get_live_quote_snapshot_delegates_and_parses() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    snapshot = {
        "symbol": "2330",
        "market": "TW",
        "name": "台積電",
        "last_price": 900.0,
        "open": 890.0,
        "high": 905.0,
        "low": 885.0,
        "volume": 1000,
        "best_bid": "899.0",
        "best_ask": "901.0",
        "observed_at": "093000",
    }
    result = SourceResult(
        source_id="TWSE_MIS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"snapshot": snapshot},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.PRICE_INTRADAY: result})
    tool = GetLiveQuoteSnapshotTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.PRICE_INTRADAY
    assert output.snapshot is not None
    assert output.snapshot.last_price == 900.0
