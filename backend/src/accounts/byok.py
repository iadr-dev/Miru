"""Bring-your-own-key resolution shared by the interactive API and the scheduler.

A caller with any saved LLM key is a BYOK caller: their LLM calls run on THEIR key, on the model
they picked, and never fall back to a platform key (which would be unmetered on our bill). If the
model's provider has no saved key the run is refused, not silently moved onto ours.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher, SecretEncryptionNotConfiguredError
from accounts.models import SecretKind
from accounts.secrets import SqlAlchemySecretStore
from config.settings import Settings
from llm.registry import ByokOverride, agent_model_spec

logger = structlog.get_logger(__name__)


class ByokKeyRequiredError(RuntimeError):
    """The caller has saved keys, but none for the provider of the model this run would use."""

    def __init__(self, providers: list[str]) -> None:
        super().__init__("no saved key for the model's provider")
        self.providers = providers


async def resolve_byok_override(
    session: AsyncSession, settings: Settings, *, user_id: str, model_override: str | None
) -> ByokOverride | None:
    """None for a normal (platform-key) run; a `ByokOverride` for a BYOK run.

    Raises `ByokKeyRequiredError` when the user has keys but not for this model's provider."""
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
        raise ByokKeyRequiredError(list(providers))
    return ByokOverride(provider_id=provider, model_id=model, api_key=key)


__all__ = ["ByokKeyRequiredError", "resolve_byok_override"]
