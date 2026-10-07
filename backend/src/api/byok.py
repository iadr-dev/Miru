"""Bring-your-own-key: decide, before a turn starts, whether it runs on the caller's own key.

The rule (ADR: the user pays inference, so the daily chat cap does not apply): a caller with any
saved LLM key is a BYOK caller. Their turns run on THEIR key, on the model they picked, for every
LLM call in the turn - and never fall back to a platform key, which would be unmetered on our bill.
If they picked a model whose provider they have no key for, the turn is refused with a clear
message instead of silently spending ours.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher, SecretEncryptionNotConfiguredError
from accounts.models import SecretKind
from accounts.secrets import SqlAlchemySecretStore
from api.errors import ApiError
from config.settings import Settings
from llm.registry import ByokOverride, agent_model_spec

logger = structlog.get_logger(__name__)


async def resolve_byok(
    session: AsyncSession, settings: Settings, *, user_id: str, model_override: str | None
) -> ByokOverride | None:
    """None for a normal (platform-key) turn; a `ByokOverride` for a BYOK turn."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return None  # nothing can have been stored, so nobody is a BYOK caller
    store = SqlAlchemySecretStore(session, cipher)
    providers = await store.llm_providers(user_id=user_id)
    if not providers:
        return None
    try:
        provider, model = agent_model_spec(model_override, settings)
    except ValueError:
        provider, model = "", ""
    key = await store.get(user_id=user_id, kind=SecretKind.LLM_KEY, provider=provider)
    if key is None:
        logger.info("byok_key_missing_for_model", user_id=user_id, provider=provider)
        raise ApiError(
            status_code=409,
            code="byok_key_required",
            message=(
                "You've added your own API key for "
                f"{', '.join(providers)}. Pick a model from one of those providers, or remove "
                "your keys to use the shared models under your plan's limits."
            ),
            retryable=False,
        )
    return ByokOverride(provider_id=provider, model_id=model, api_key=key)


async def byok_active_for(session: AsyncSession, settings: Settings, *, user_id: str) -> bool:
    """Whether the caller has any saved LLM key - the flag that lifts the daily chat cap. A missing
    encryption config means no key can exist, so the answer is False (never over-grants)."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return False
    return await SqlAlchemySecretStore(session, cipher).has_llm_key(user_id=user_id)


__all__ = ["byok_active_for", "resolve_byok"]
