"""The single place that knows the Paddle price-ID vocabulary.

A Paddle subscription is only ever a `price_id`; every other layer speaks in terms of a
`(tier, interval, byok)` triple. This module owns the two-way mapping between them, built from
config — no price ID is ever hardcoded here or anywhere else. `resolve_plan` turns a webhook's
`price_id` into the tier/BYOK entitlement to apply; `price_id_for` turns a pricing-page
selection into the `price_id` a checkout is created against.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from config.settings import Settings
from schemas.enums import PaidTier, Tier


class BillingInterval(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class PlanInfo(BaseModel):
    """The entitlement a given `price_id` grants: a paid tier, a billing cadence, and whether it
    is the BYOK variant (the user supplies their own LLM key at a lower price)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price_id: str
    tier: PaidTier
    interval: BillingInterval
    byok: bool


class UnknownPriceError(LookupError):
    """A `price_id` that maps to none of the configured plans — a webhook or a checkout request
    referencing a price we don't recognize is refused rather than silently mapped to a tier."""


class PlanNotConfiguredError(RuntimeError):
    """The requested `(tier, interval, byok)` plan has no `price_id` configured in the
    environment — surfaced rather than defaulting to some other plan."""


# The 8 paid plans keyed by (tier, interval, byok), each naming the Settings field its price ID
# comes from. Free is not a purchasable plan (it is the absence of an active subscription), so it
# never appears here.
_SETTINGS_FIELD: Final[dict[tuple[PaidTier, BillingInterval, bool], str]] = {
    (Tier.PREMIUM, BillingInterval.MONTHLY, False): "paddle_price_premium_monthly",
    (Tier.PREMIUM, BillingInterval.ANNUAL, False): "paddle_price_premium_annual",
    (Tier.PREMIUM, BillingInterval.MONTHLY, True): "paddle_price_premium_byok_monthly",
    (Tier.PREMIUM, BillingInterval.ANNUAL, True): "paddle_price_premium_byok_annual",
    (Tier.PRO, BillingInterval.MONTHLY, False): "paddle_price_pro_monthly",
    (Tier.PRO, BillingInterval.ANNUAL, False): "paddle_price_pro_annual",
    (Tier.PRO, BillingInterval.MONTHLY, True): "paddle_price_pro_byok_monthly",
    (Tier.PRO, BillingInterval.ANNUAL, True): "paddle_price_pro_byok_annual",
}


def _configured_plans(settings: Settings) -> list[PlanInfo]:
    plans: list[PlanInfo] = []
    for (tier, interval, byok), field in _SETTINGS_FIELD.items():
        price_id = getattr(settings, field)
        if price_id:
            plans.append(PlanInfo(price_id=price_id, tier=tier, interval=interval, byok=byok))
    return plans


def all_plans(settings: Settings) -> list[PlanInfo]:
    """Every plan that has a `price_id` configured — the catalog `GET /billing/plans` exposes."""
    return _configured_plans(settings)


def resolve_plan(price_id: str, settings: Settings) -> PlanInfo:
    """Map a Paddle `price_id` (from a webhook or a subscription item) to its entitlement."""
    for plan in _configured_plans(settings):
        if plan.price_id == price_id:
            return plan
    raise UnknownPriceError(price_id)


def price_id_for(tier: Tier, interval: BillingInterval, *, byok: bool, settings: Settings) -> str:
    """Map a pricing-page selection to the configured `price_id` a checkout is created against."""
    if tier is Tier.FREE:  # free has no purchasable price
        raise PlanNotConfiguredError(f"no plan for {tier.value}/{interval.value}/byok={byok}")
    field = _SETTINGS_FIELD.get((tier, interval, byok))
    if field is None:
        raise PlanNotConfiguredError(f"no plan for {tier.value}/{interval.value}/byok={byok}")
    price_id = getattr(settings, field)
    if not price_id:
        raise PlanNotConfiguredError(field)
    return str(price_id)


__all__ = [
    "BillingInterval",
    "PlanInfo",
    "PlanNotConfiguredError",
    "UnknownPriceError",
    "all_plans",
    "price_id_for",
    "resolve_plan",
]
