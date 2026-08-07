from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.health_tools import GetSourceHealthTool, GetSourceMatrixInput, GetSourceMatrixTool


def _ok_result(cap: Capability) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=f"caveat for {cap.value}",
        payload={"bars": [{"close": 1.0}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_source_health_reports_all_ok() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD),
            Capability.FINANCIALS: _ok_result(Capability.FINANCIALS),
            Capability.DIVIDENDS: _ok_result(Capability.DIVIDENDS),
            Capability.INSTITUTIONAL_FLOW: _ok_result(Capability.INSTITUTIONAL_FLOW),
            Capability.ANALYST_CONSENSUS: _ok_result(Capability.ANALYST_CONSENSUS),
            Capability.SECTOR_CLASS: _ok_result(Capability.SECTOR_CLASS),
        },
    )
    tool = GetSourceHealthTool(router)
    args = tool.input_model(id="2330", market=Market.TW)

    output = await tool.run(args, ToolContext())

    assert output.all_ok
    assert output.entries[0].status is HealthStatus.OK


async def test_get_source_matrix_reads_router_matrix_without_symbol() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD)})
    tool = GetSourceMatrixTool(router)

    output = await tool.run(GetSourceMatrixInput(), ToolContext())

    assert any(e.source_id == adapter.source_id for e in output.entries)
    assert any(
        e.capability is Capability.PRICE_EOD and e.market is Market.TW for e in output.entries
    )
