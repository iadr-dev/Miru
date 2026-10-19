"""Bring-your-own-key: decide, before a turn starts, whether it runs on the caller's own key.

The rule (ADR: the user pays inference, so the daily chat cap does not apply): a caller with any
saved LLM key is a BYOK caller. Their turns run on THEIR key, on the model they picked, for every
LLM call in the turn - and never fall back to a platform key, which would be unmetered on our bill.
If they picked a model whose provider they have no key for, the turn is refused with a clear
message instead of silently spending ours.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from accounts.byok import ByokKeyRequiredError, resolve_byok_override
from accounts.entitlements import platform_model_allowed
from accounts.models import User
from api.errors import ApiError
from config.settings import Settings
from llm.registry import ByokOverride


async def resolve_byok(
    session: AsyncSession, settings: Settings, *, user_id: str, model_override: str | None
) -> ByokOverride | None:
    """None for a normal (platform-key) turn; a `ByokOverride` for a BYOK turn."""
    try:
        return await resolve_byok_override(
            session, settings, user_id=user_id, model_override=model_override
        )
    except ByokKeyRequiredError as exc:
        raise ApiError(
            status_code=409,
            code="byok_key_required",
            message=(
                "You've added your own API key for "
                f"{', '.join(exc.providers)}. Pick a model from one of those providers, or remove "
                "your keys to use the shared models under your plan's limits."
            ),
            retryable=False,
        ) from exc


def enforce_platform_model(user: User, model_override: str | None, *, byok: bool) -> None:
    """A turn on the platform key may only use the models the caller's plan includes."""
    if not platform_model_allowed(user, model_override, byok_active=byok):
        raise ApiError(
            status_code=403,
            code="model_requires_upgrade",
            message="That model is included with a paid plan. Pick a lighter model, or upgrade.",
            retryable=False,
        )


__all__ = ["enforce_platform_model", "resolve_byok"]
