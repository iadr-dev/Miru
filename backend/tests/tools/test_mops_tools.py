from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import SymbolInput
from tools.mops_tools import GetInsiderShareholdingTool, GetMaterialAnnouncementsTool


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="MOPS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_material_announcements_delegates_and_parses() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "company_name": "TSMC",
        "announced_date": "2026-08-01",
        "announced_time": "09:00:00",
        "subject": "subject",
        "detail": "detail",
        "qualifying_clause": None,
    }
    router, adapter = make_router(
        symbol, {Capability.CORPORATE_ACTIONS: _result({"announcements": [row]})}
    )
    tool = GetMaterialAnnouncementsTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.CORPORATE_ACTIONS
    assert output.announcements[0].company_name == "TSMC"


async def test_get_insider_shareholding_delegates_and_parses() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "as_of": "2026-06",
        "person_name": "Someone",
        "title": "董事長本人",
        "shares_at_election": 100,
        "current_shares": 90,
        "pledged_shares": 0,
        "pledged_ratio": "0.00%",
    }
    router, adapter = make_router(symbol, {Capability.SHAREHOLDING: _result({"holdings": [row]})})
    tool = GetInsiderShareholdingTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.SHAREHOLDING
    assert output.holdings[0].pledged_ratio == "0.00%"
