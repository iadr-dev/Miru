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
    "deep_research": Tier.PRO,
    # Tick and minute-K are sponsor-class data, which the tool-dispatch gate unlocks at Premium and
    # above - so this row says Premium, matching what is actually enforced.
    "tick_minute_k": Tier.PREMIUM,
}


class TierSummary(BaseModel):
    """One tier as the pricing page shows it - a projection of the two tables above, so the page
    can never promise a limit or feature the enforcement code does not know about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: Tier
    chat_limit_per_day: int | None  # None = unlimited
    sponsor_data: bool  # branch/ownership/holder data unmasked
    features: tuple[str, ...]


def tier_catalog() -> list[TierSummary]:
    """Free, Premium and Pro side by side, in order."""
    summaries: list[TierSummary] = []
    for tier in (Tier.FREE, Tier.PREMIUM, Tier.PRO):
        limits = TIER_LIMITS[tier]
        features = tuple(
            sorted(f for f, minimum in FEATURE_MIN_TIER.items() if _tier_at_least(tier, minimum))
        )
        summaries.append(
            TierSummary(
                tier=tier,
                chat_limit_per_day=limits.chat_limit_per_day,
                sponsor_data=limits.finmind_class is not Tier.FREE,
                features=features,
            )
        )
    return summaries


def _tier_at_least(tier: Tier, minimum: Tier) -> bool:
    return _TIER_RANK[tier] >= _TIER_RANK[minimum]


def chat_limit_for(user: User, *, byok_active: bool) -> int | None:
    """Daily AI-chat cap. `None` = unlimited. BYOK lifts the cap (the user pays inference) —
    this is the ONLY thing BYOK changes about entitlement."""
    if byok_active:
        return None
    return TIER_LIMITS[user.tier].chat_limit_per_day


# Voice (speech-to-text, read-aloud) is Free for everyone. A per-minute rate always applies (abuse /
# CPU protection); the per-UTC-day cap applies only when the configured provider bills per call
# (OpenAI TTS) - local speech-to-text and Edge voices cost nothing, so they have no daily quota.
VOICE_REQUESTS_PER_MINUTE: Final[int] = 20
VOICE_DAILY_LIMIT: Final[int] = 100

# Models a Free account may pick when the turn runs on OUR key (BYOK turns run on the caller's own
# key and are unrestricted). These are the low-cost ones; the heavier models need a paid plan, so a
# 3-chats-a-day account cannot spend the premium-model price on every turn. The platform default
# model (no pick) is always allowed. Keep in step with the picker catalog in the web composer.
FREE_PLATFORM_MODELS: Final[frozenset[str]] = frozenset(
    {
        "anthropic/claude-haiku-4-5-20251001",
        "openai/gpt-4o-mini",
        "openai/gpt-5.4-mini",
        "gemini/gemini-3.5-flash-lite",
        "gemini/gemini-3.5-flash",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "openrouter/deepseek/deepseek-chat-v3.1",
        "openrouter/deepseek/deepseek-v4-flash",
        "ollama/qwen3.6:latest",
    }
)


def platform_models_for(user: User, *, byok_active: bool) -> tuple[str, ...] | None:
    """The models `user` may pick on the platform key, or `None` for no restriction."""
    if byok_active or user.tier is not Tier.FREE:
        return None
    return tuple(sorted(FREE_PLATFORM_MODELS))


def platform_model_allowed(user: User, model_override: str | None, *, byok_active: bool) -> bool:
    allowed = platform_models_for(user, byok_active=byok_active)
    return allowed is None or model_override is None or model_override in allowed


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
        finmind_class=finmind_class_for(user),
        byok_active=byok_active,
        features=allowed_features(user),
        platform_models=platform_models_for(user, byok_active=byok_active),
    )


__all__ = [
    "FEATURE_MIN_TIER",
    "FREE_PLATFORM_MODELS",
    "TIER_LIMITS",
    "VOICE_DAILY_LIMIT",
    "VOICE_REQUESTS_PER_MINUTE",
    "TierLimits",
    "TierSummary",
    "allowed_features",
    "chat_limit_for",
    "entitlement_view",
    "feature_allowed",
    "finmind_class_for",
    "platform_model_allowed",
    "platform_models_for",
    "rate_limit_per_minute",
    "tier_catalog",
]
