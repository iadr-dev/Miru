"""Proves `dispatch_tool` masks `get_chip_flows` for FREE and passes through real data for
PREMIUM/PRO — the sponsor-tier worked example of entitlement enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import DateRangeInput, SymbolInput
from tools.market_tools import GetChipFlowsOutput, GetChipFlowsTool, GetPriceQuoteTool


def _chip_broker_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_dispatch_masks_chip_flows_for_free_tier() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    tool = GetChipFlowsTool(router)
    args = DateRangeInput(id="2330", market=Market.TW)

    result = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))

    assert isinstance(result, MaskedTeaser)
    assert result.locked is True
    assert result.tool_name == "get_chip_flows"


async def test_dispatch_returns_real_data_for_premium_and_pro() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    tool = GetChipFlowsTool(router)
    args = DateRangeInput(id="2330", market=Market.TW)

    for tier in (Tier.PREMIUM, Tier.PRO):
        result = await dispatch_tool(tool, args, ToolContext(tier=tier))
        assert isinstance(result, GetChipFlowsOutput)


async def test_dispatch_never_masks_public_tools() -> None:
    """A `public` tool always runs regardless of tier — the gate only fires for `sponsor`."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {Capability.PRICE_EOD: _chip_broker_result()})
    tool = GetPriceQuoteTool(router)
    args = SymbolInput(id="2330", market=Market.TW)

    result = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))

    assert not isinstance(result, MaskedTeaser)
