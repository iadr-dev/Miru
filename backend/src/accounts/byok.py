"""Bring-your-own-key resolution shared by the interactive API and the scheduler.

A caller with any saved LLM key is a BYOK caller: their LLM calls run on THEIR key, on the model
they picked (or their provider's curated default), and never fall back to a platform key (which
would be unmetered on our bill). If the model's provider has no saved key the run is refused,
not silently moved onto ours.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher, SecretEncryptionNotConfiguredError
from accounts.models import SecretKind
from accounts.secrets import SqlAlchemySecretStore
from config.settings import Settings
from llm.byok_catalog import InvalidModelSpecError, UnknownModelError, validate_spec
from llm.byok_profiles import BYOK_PROVIDER_ORDER, provider_tiers
from llm.registry import ByokOverride

logger = structlog.get_logger(__name__)


class ByokKeyRequiredError(RuntimeError):
    """The caller has saved keys, but none for the provider of the model this run would use."""

    def __init__(self, providers: list[str]) -> None:
        super().__init__("no saved key for the model's provider")
        self.providers = providers


async def byok_active(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    subscription_byok: bool | None = None,
) -> bool:
    """Whether the user is a BYOK caller - the one flag that lifts the chat limit. A missing/invalid
    encryption config never crashes a read: it means no key can be resolved, so the user counts as
    non-BYOK (never over-granting).

    By default that is "has any saved LLM key". For a billing view pass `subscription_byok` (the
    subscription's BYOK flag): a BYOK subscription only counts while a key is actually on file, and
    a key alone does not make a non-BYOK subscription BYOK, so the answer is `subscription_byok and
    has_key`. A subscription that is not BYOK short-circuits without reading the keys."""
    if subscription_byok is False:
        return False
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return False
    return await SqlAlchemySecretStore(session, cipher).has_llm_key(user_id=user_id)


def _parse_model_spec(spec: str | None) -> tuple[str, str] | None:
    if not spec:
        return None
    provider, _, model = spec.partition("/")
    return (provider, model) if provider and model else None


async def byok_providers(session: AsyncSession, settings: Settings, *, user_id: str) -> list[str]:
    """The providers the user has a saved LLM key for ([] when none, or encryption is off)."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return []
    return list(await SqlAlchemySecretStore(session, cipher).llm_providers(user_id=user_id))


def _in_catalog(spec: str | None) -> bool:
    """A saved default that has since left the curated catalog is stale, not usable."""
    try:
        validate_spec(spec or "")
    except (InvalidModelSpecError, UnknownModelError):
        return False
    return True


def effective_main(providers: list[str], default_model: str | None) -> tuple[str, str] | None:
    """The `(provider, model)` a BYOK user runs on when they pick nothing: their saved
    `default_model` while its provider still has a key, else the `default_main` of the first
    provider (`BYOK_PROVIDER_ORDER`) they hold a key for. None when no provider qualifies."""
    saved = _parse_model_spec(default_model)
    if saved is not None and saved[0] in providers and _in_catalog(default_model):
        return saved
    provider = next((p for p in BYOK_PROVIDER_ORDER if p in providers), "")
    tiers = provider_tiers(provider)
    return (provider, tiers.default_main) if tiers is not None else None


async def resolve_byok_override(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    model_override: str | None,
    default_model: str | None = None,
) -> ByokOverride | None:
    """None for a normal (platform-key) run; a `ByokOverride` for a BYOK run.

    The main model is, in order: a well-formed `model_override` ("provider/model", the request or
    thread pick), else the user's saved `default_model` when its provider still has a key, else the
    `default_main` of the first provider the user holds a key for (`BYOK_PROVIDER_ORDER`). The
    cheap model is that provider's curated cheap tier. The platform's `MODEL_ROLE_*` config is never
    consulted.

    Raises `ByokKeyRequiredError` when the user has keys but none for the requested provider."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return None  # nothing can have been stored, so nobody is a BYOK caller
    store = SqlAlchemySecretStore(session, cipher)
    providers = await store.llm_providers(user_id=user_id)
    if not providers:
        return None

    picked = _parse_model_spec(model_override)
    if picked is not None:
        provider, model = picked
        if provider not in providers:
            logger.info("byok_key_missing_for_model", user_id=user_id, provider=provider)
            raise ByokKeyRequiredError(list(providers))
    else:
        chosen = effective_main(list(providers), default_model)
        if chosen is None:
            logger.info("byok_no_usable_provider", user_id=user_id)
            raise ByokKeyRequiredError(list(providers))
        provider, model = chosen

    key = await store.get(user_id=user_id, kind=SecretKind.LLM_KEY, provider=provider)
    if key is None:
        logger.info("byok_key_missing_for_model", user_id=user_id, provider=provider)
        raise ByokKeyRequiredError(list(providers))
    tiers = provider_tiers(provider)
    cheap = tiers.cheap if tiers is not None else model
    return ByokOverride(provider_id=provider, api_key=key, main_model=model, cheap_model=cheap)


__all__ = [
    "ByokKeyRequiredError",
    "byok_active",
    "byok_providers",
    "effective_main",
    "resolve_byok_override",
]
