"""The single source of truth for tier limits + feature gating — the one module both the API
and the agent consult. Two of the three "capability resolution" axes live here:

1. **Data-licensing** (`data_class = public | sponsor`) — resolved by `finmind_class_for` +
   the pure `tools.base.is_entitled` predicate the tool-dispatch gate already applies. This
   module supplies the *tier*; `tools.base.dispatch_tool` owns the masking mechanism. There is
   no second, redundant sponsor gate here — see `accounts.hooks` for how the resolved tier is
   fed to `dispatch_tool`.
2. **Tier-feature** (`feature_allowed`) — a feature→min-tier lookup.

The third axis (per-turn consent, e.g. the web-search toggle) is request-scoped and not a tier
concern, so it is not modeled here.

Tier→limits and feature→min-tier are **config tables**, not scattered constants — a new gated
feature is one row here, consulted identically everywhere.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict

from accounts.models import EntitlementView, User
from schemas.enums import Tier

# Ordering for min-tier comparisons — Free < Premium < Pro.
_TIER_RANK: Final[dict[Tier, int]] = {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2}


class TierLimits(BaseModel):
    """Per-tier throughput/quota knobs. `chat_limit_per_day = None` means unlimited (Pro). BYOK
    independently lifts the chat limit — see `chat_limit_for`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_limit_per_day: int | None
    requests_per_minute: int
    finmind_class: Tier  # FREE → free FinMind token; PREMIUM/PRO → sponsor pool


# `finmind_class` PREMIUM/PRO both map to the sponsor token pool.
TIER_LIMITS: Final[dict[Tier, TierLimits]] = {
    Tier.FREE: TierLimits(chat_limit_per_day=3, requests_per_minute=60, finmind_class=Tier.FREE),
    Tier.PREMIUM: TierLimits(
        chat_limit_per_day=100, requests_per_minute=300, finmind_class=Tier.PREMIUM
    ),
    Tier.PRO: TierLimits(
        chat_limit_per_day=None, requests_per_minute=600, finmind_class=Tier.PREMIUM
    ),
}

# Feature → minimum tier. A feature absent from this table is unrestricted (available to every
# tier, incl. anonymous/Free). Voice STT and TTS read-aloud are both deliberately NOT here — both
# are Free, quota-bounded rather than tier-gated (voice conversation mode is built on both, and
# is a Free-tier feature end to end).
FEATURE_MIN_TIER: Final[dict[str, Tier]] = {
    "live_quote_stream": Tier.PREMIUM,
    "ai_summary": Tier.PREMIUM,
    "swot": Tier.PREMIUM,
    "full_score": Tier.PREMIUM,
    "news_sentiment": Tier.PREMIUM,
    "theme_discovery": Tier.PREMIUM,
    "deep_research": Tier.PRO,
    "scheduler": Tier.PRO,
    "tick_minute_k": Tier.PRO,
}


def _tier_at_least(tier: Tier, minimum: Tier) -> bool:
    return _TIER_RANK[tier] >= _TIER_RANK[minimum]


def limits_for(tier: Tier) -> TierLimits:
    return TIER_LIMITS[tier]


def chat_limit_for(user: User, *, byok_active: bool) -> int | None:
    """Daily AI-chat cap. `None` = unlimited. BYOK lifts the cap (the user pays inference) —
    this is the ONLY thing BYOK changes about entitlement."""
    if byok_active:
        return None
    return TIER_LIMITS[user.tier].chat_limit_per_day


def rate_limit_per_minute(user: User) -> int:
    return TIER_LIMITS[user.tier].requests_per_minute


def finmind_class_for(user: User) -> Tier:
    """The data-licensing tier that drives `sponsor` unmasking. Returns a `Tier` because that
    is exactly what `tools.base.is_entitled`/`ToolContext.tier` consume — a Premium/Pro user
    resolves to a sponsor-entitled tier, a Free user does not."""
    return TIER_LIMITS[user.tier].finmind_class


def feature_allowed(user: User, feature: str) -> bool:
    """Axis-2 feature gate: is `feature` available at this user's tier? Unlisted features are
    unrestricted."""
    minimum = FEATURE_MIN_TIER.get(feature)
    if minimum is None:
        return True
    return _tier_at_least(user.tier, minimum)


def allowed_features(user: User) -> tuple[str, ...]:
    return tuple(sorted(f for f in FEATURE_MIN_TIER if feature_allowed(user, f)))


def entitlement_view(user: User, *, byok_active: bool) -> EntitlementView:
    """The resolved envelope `GET /me` returns and the frontend renders CTAs from."""
    return EntitlementView(
        tier=user.tier,
        chat_limit_per_day=chat_limit_for(user, byok_active=byok_active),
        requests_per_minute=rate_limit_per_minute(user),
        finmind_class=finmind_class_for(user),
        byok_active=byok_active,
        features=allowed_features(user),
    )


__all__ = [
    "FEATURE_MIN_TIER",
    "TIER_LIMITS",
    "TierLimits",
    "allowed_features",
    "chat_limit_for",
    "entitlement_view",
    "feature_allowed",
    "finmind_class_for",
    "limits_for",
    "rate_limit_per_minute",
]
