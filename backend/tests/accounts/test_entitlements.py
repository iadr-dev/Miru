"""Entitlement resolution + the sponsor-masking integration.

The masking *mechanism* is `tools.base.dispatch_tool` (already built in Phase 4); these tests
prove the Phase-9 half — that a real per-user tier resolved by `EntitlementResolver` flows into
that gate so a Free user gets a `MaskedTeaser` on a `sponsor` tool while a Premium user gets the
real result.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from accounts.entitlements import (
    chat_limit_for,
    entitlement_view,
    feature_allowed,
    finmind_class_for,
)
from accounts.hooks import EntitlementResolver
from accounts.models import User
from schemas.enums import DataClass, Tier
from tools.base import BaseTool, MaskedTeaser, dispatch_tool


def _user(tier: Tier) -> User:
    now = datetime.now(UTC)
    return User(id="u1", tier=tier, created_at=now, updated_at=now)


class _SponsorOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str


class _SponsorArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SponsorTool(BaseTool):
    name = "sponsor_chip_flows"
    description = "sponsor-only data"
    input_model = _SponsorArgs
    output_model = _SponsorOut
    data_class = DataClass.SPONSOR

    async def run(self, args: BaseModel, ctx: object) -> BaseModel:
        return _SponsorOut(value="real sponsor data")


def test_tier_limits_match_spec() -> None:
    assert chat_limit_for(_user(Tier.FREE), byok_active=False) == 3
    assert chat_limit_for(_user(Tier.PREMIUM), byok_active=False) == 100
    assert chat_limit_for(_user(Tier.PRO), byok_active=False) is None


def test_byok_lifts_chat_limit() -> None:
    assert chat_limit_for(_user(Tier.FREE), byok_active=True) is None


def test_finmind_class_and_features() -> None:
    assert finmind_class_for(_user(Tier.FREE)) is Tier.FREE
    assert finmind_class_for(_user(Tier.PREMIUM)) is Tier.PREMIUM
    assert not feature_allowed(_user(Tier.FREE), "deep_research")
    assert not feature_allowed(_user(Tier.PREMIUM), "deep_research")
    assert feature_allowed(_user(Tier.PRO), "deep_research")
    assert feature_allowed(_user(Tier.PREMIUM), "ai_summary")
    # Unlisted feature is unrestricted.
    assert feature_allowed(_user(Tier.FREE), "watchlist")


def test_entitlement_view_shape() -> None:
    view = entitlement_view(_user(Tier.PREMIUM), byok_active=False)
    assert view.tier is Tier.PREMIUM
    assert "ai_summary" in view.features
    assert "deep_research" not in view.features


@pytest.mark.governance
async def test_free_user_gets_masked_teaser_on_sponsor_tool() -> None:
    resolver = EntitlementResolver()
    ctx = resolver.tool_context_for(_user(Tier.FREE))
    result = await dispatch_tool(SponsorTool(), _SponsorArgs(), ctx)
    assert isinstance(result, MaskedTeaser)
    assert result.required_tier is Tier.PREMIUM


@pytest.mark.governance
async def test_premium_user_gets_real_sponsor_data() -> None:
    resolver = EntitlementResolver()
    ctx = resolver.tool_context_for(_user(Tier.PREMIUM))
    result = await dispatch_tool(SponsorTool(), _SponsorArgs(), ctx)
    assert isinstance(result, _SponsorOut)
    assert result.value == "real sponsor data"


@pytest.mark.governance
async def test_anonymous_caller_is_free_tier() -> None:
    resolver = EntitlementResolver()
    ctx = resolver.tool_context_for(None)
    assert ctx.tier is Tier.FREE
    result = await dispatch_tool(SponsorTool(), _SponsorArgs(), ctx)
    assert isinstance(result, MaskedTeaser)


def test_tier_catalog_is_a_projection_of_the_enforced_tables() -> None:
    from accounts.entitlements import FEATURE_MIN_TIER, TIER_LIMITS, tier_catalog  # noqa: PLC0415

    free, premium, pro = tier_catalog()
    assert (free.tier, premium.tier, pro.tier) == (Tier.FREE, Tier.PREMIUM, Tier.PRO)
    for summary in (free, premium, pro):
        limits = TIER_LIMITS[summary.tier]
        assert summary.chat_limit_per_day == limits.chat_limit_per_day
        assert summary.requests_per_minute == limits.requests_per_minute
    # a higher tier never loses a feature, and every listed feature is a real gated one
    assert set(free.features) <= set(premium.features) <= set(pro.features)
    assert set(pro.features) == set(FEATURE_MIN_TIER)
    assert (free.sponsor_data, premium.sponsor_data, pro.sponsor_data) == (False, True, True)
    # deep research is Pro-only, and the limit shown is the one that is enforced
    assert "deep_research" not in premium.features
    assert pro.deep_research_limit_per_day == 5
    assert free.deep_research_limit_per_day is None
