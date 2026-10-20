"""`ai_summary` (Premium in `FEATURE_MIN_TIER`) is enforced by the sponsor-class gate in
`dispatch_tool`, the one gate REST, the agent and MCP share. The table row and the tool's data
class must agree, or the pricing page would promise a tier the tool does not enforce."""

from __future__ import annotations

from unittest.mock import AsyncMock

from schemas.enums import DataClass, Tier
from schemas.feature_tiers import FEATURE_MIN_TIER, access_for
from tools.base import MaskedTeaser, ToolContext, dispatch_tool, is_entitled
from tools.summary_tools import GetStockSummaryInput, GetStockSummaryTool


def _tool() -> GetStockSummaryTool:
    return GetStockSummaryTool(AsyncMock(), AsyncMock())


def test_the_row_says_premium_and_the_tool_is_sponsor_class() -> None:
    assert FEATURE_MIN_TIER["ai_summary"] is Tier.PREMIUM
    assert _tool().data_class is DataClass.SPONSOR
    for tier in Tier:
        assert is_entitled(DataClass.SPONSOR, tier) == access_for("ai_summary", tier).full


async def test_free_gets_the_locked_teaser_not_an_error() -> None:
    result = await dispatch_tool(
        _tool(), GetStockSummaryInput(id="2330", market="TW"), ToolContext(tier=Tier.FREE)
    )
    assert isinstance(result, MaskedTeaser)
    assert result.locked is True
    assert result.required_tier is Tier.PREMIUM
