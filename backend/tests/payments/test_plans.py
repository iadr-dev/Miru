"""Price-ID <-> (tier, interval, byok) mapping, both directions, plus the unknown/unconfigured
failure modes."""

from __future__ import annotations

import pytest

from config.settings import Settings
from payments.plans import (
    BillingInterval,
    PlanNotConfiguredError,
    UnknownPriceError,
    all_plans,
    price_id_for,
    resolve_plan,
)
from schemas.enums import Tier
from tests.payments.conftest import (
    PRICE_PREMIUM_BYOK_MONTHLY,
    PRICE_PRO_ANNUAL,
)


def test_all_plans_lists_configured(settings: Settings) -> None:
    plans = all_plans(settings)
    assert len(plans) == 8
    assert {p.tier for p in plans} == {Tier.PREMIUM, Tier.PRO}


def test_resolve_plan_maps_price_to_entitlement(settings: Settings) -> None:
    plan = resolve_plan(PRICE_PREMIUM_BYOK_MONTHLY, settings)
    assert plan.tier is Tier.PREMIUM
    assert plan.interval is BillingInterval.MONTHLY
    assert plan.byok is True


def test_price_id_for_round_trips(settings: Settings) -> None:
    price_id = price_id_for(Tier.PRO, BillingInterval.ANNUAL, byok=False, settings=settings)
    assert price_id == PRICE_PRO_ANNUAL
    assert resolve_plan(price_id, settings).tier is Tier.PRO


def test_resolve_unknown_price_raises(settings: Settings) -> None:
    with pytest.raises(UnknownPriceError):
        resolve_plan("pri_does_not_exist", settings)


def test_price_id_for_unconfigured_plan_raises() -> None:
    # Explicitly None so the test never depends on whatever a developer's .env holds.
    bare = Settings(paddle_price_premium_monthly=None)
    with pytest.raises(PlanNotConfiguredError):
        price_id_for(Tier.PREMIUM, BillingInterval.MONTHLY, byok=False, settings=bare)


def test_free_tier_has_no_purchasable_price(settings: Settings) -> None:
    with pytest.raises(PlanNotConfiguredError):
        price_id_for(Tier.FREE, BillingInterval.MONTHLY, byok=False, settings=settings)
