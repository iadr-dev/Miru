from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import DateRangeInput
from tools.convertible_bond_tools import GetConvertibleBondFlowTool, GetConvertibleBondProfileTool


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_convertible_bond_profile_bundles_three_datasets_and_is_sponsor() -> None:
    symbol = Symbol(id="12345", market=Market.TW)
    info_row = {"symbol": "12345", "market": "TW", "name": "Some CB"}
    router, _ = make_router(
        symbol,
        {
            Capability.CB_INFO: _result({"rows": [info_row]}),
            Capability.CB_DAILY_OVERVIEW: _result({"rows": []}),
            Capability.CB_PUT_PROVISION: _result({"rows": []}),
        },
    )
    tool = GetConvertibleBondProfileTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = DateRangeInput(id="12345", market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)

    output = await tool.run(args, ToolContext())
    assert output.info[0].name == "Some CB"


async def test_get_convertible_bond_flow_bundles_three_datasets() -> None:
    symbol = Symbol(id="12345", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.CB_DAILY: _result({"rows": []}),
            Capability.CB_INSTITUTIONAL_FLOW: _result({"rows": []}),
            Capability.CB_MONTHLY_ANALYSIS: _result({"rows": []}),
        },
    )
    tool = GetConvertibleBondFlowTool(router)

    output = await tool.run(DateRangeInput(id="12345", market=Market.TW), ToolContext())

    assert output.daily == ()
    assert output.institutional_flow == ()
    assert output.monthly_analysis == ()
