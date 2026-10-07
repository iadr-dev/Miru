"""Accounts extras: profile update, agent-voice preset catalog, usage/quota, and BYOK keys.

Profile + presets wrap the already-built `UserStore`/`PersonalityRegistry`. Usage-quota is a
projection of `accounts.entitlements` (the one tier→limits table) plus the caller's live BYOK
state — it reports the *limits* the caller is subject to. BYOK key storage wraps
`accounts.secrets.SecretStore`, which Fernet-encrypts every value at rest and never logs the
plaintext; this router only ever forwards the raw key straight into `put(...)` and never binds it
to a log field.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher, SecretEncryptionNotConfiguredError
from accounts.entitlements import deep_research_limit_per_day, entitlement_view
from accounts.models import EntitlementView, SecretKind, User
from accounts.secrets import SqlAlchemySecretStore
from accounts.store import UserStore
from api.dependencies import (
    AppState,
    get_session,
    get_settings_dep,
    get_state,
    get_user_store,
    require_user,
)
from api.errors import ApiError
from config.settings import Settings
from llm.registry import BYOK_PROVIDERS
from personality.models import PersonalityPreset
from usage.llm_usage import MAX_DAYS, LlmUsageSummary, summarize_llm_usage

router = APIRouter(tags=["accounts"])


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    birthday: str | None = None
    phone_country_code: str | None = None
    phone_number: str | None = None
    nationality: str | None = None
    personality_id: str | None = None


class ProfileResponse(BaseModel):
    user: User
    entitlements: EntitlementView


async def _byok_active(session: AsyncSession, settings: Settings, *, user_id: str) -> bool:
    """Whether the caller has any BYOK LLM key on file — the one flag that lifts the chat limit.
    A missing/invalid encryption config never crashes a read: it simply means no BYOK key can be
    resolved, so the caller is treated as non-BYOK (never over-granting)."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return False
    return await SqlAlchemySecretStore(session, cipher).has_llm_key(user_id=user_id)


@router.patch("/me")
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(require_user),
    store: UserStore = Depends(get_user_store),
    session: AsyncSession = Depends(get_session),
    state: AppState = Depends(get_state),
    settings: Settings = Depends(get_settings_dep),
) -> ProfileResponse:
    if body.personality_id is not None and body.personality_id not in set(
        state.personality_registry.known_ids()
    ):
        raise ApiError(
            status_code=422,
            code="unknown_personality",
            message=f"Unknown personality preset {body.personality_id!r}.",
            retryable=False,
        )
    updated = await store.update_profile(
        user_id=user.id,
        display_name=body.display_name,
        birthday=body.birthday,
        phone_country_code=body.phone_country_code,
        phone_number=body.phone_number,
        nationality=body.nationality,
        personality_id=body.personality_id,
    )
    byok = await _byok_active(session, settings, user_id=user.id)
    return ProfileResponse(user=updated, entitlements=entitlement_view(updated, byok_active=byok))


@router.get("/personality/presets")
async def personality_presets(
    state: AppState = Depends(get_state),
) -> dict[str, list[PersonalityPreset]]:
    """The agent-voice presets for the Settings picker (id, names, description, tone)."""
    return {"presets": state.personality_registry.all_presets()}


@router.get("/usage/quota")
async def usage_quota(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> EntitlementView:
    """The caller's resolved limits (chats/day, req/min, FinMind class) given tier + BYOK. This
    is the entitlement envelope; live consumption counters are not surfaced here (no durable
    usage-rollup store is wired yet — see the report)."""
    byok = await _byok_active(session, settings, user_id=user.id)
    return entitlement_view(user, byok_active=byok)


class TodayUsage(BaseModel):
    chats_used: int
    deep_research_used: int
    deep_research_limit: int


@router.get("/usage/today")
async def usage_today(
    user: User = Depends(require_user), state: AppState = Depends(get_state)
) -> TodayUsage:
    """What the caller has claimed of today's (UTC) allowances - the consumption half of the
    limits `/usage/quota` reports."""
    counts = await state.usage_store.today_counts(user_id=user.id)
    return TodayUsage(
        chats_used=counts["chats"],
        deep_research_used=counts["deep_research"],
        deep_research_limit=deep_research_limit_per_day(user),
    )


@router.get("/usage/llm")
async def usage_llm(
    days: int = Query(default=30, ge=1, le=MAX_DAYS),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> LlmUsageSummary:
    """The caller's own LLM consumption over the last `days` days, from each provider's per-response
    usage metadata (daily series, per-model totals; cost only where the provider reports it)."""
    return await summarize_llm_usage(session, user_id=user.id, days=days)


class KeyProviders(BaseModel):
    providers: list[str]


@router.get("/me/keys")
async def list_keys(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> KeyProviders:
    """Which providers the caller has saved an LLM key for - names only, never the keys. The chat
    model picker uses this: a caller with keys can only run models from those providers."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return KeyProviders(providers=[])
    return KeyProviders(
        providers=await SqlAlchemySecretStore(session, cipher).llm_providers(user_id=user.id)
    )


class PutKeyRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    kind: SecretKind = SecretKind.LLM_KEY
    key: str = Field(min_length=1)


@router.post("/me/keys", status_code=status.HTTP_204_NO_CONTENT)
async def put_key(
    body: PutKeyRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """Store an encrypted BYOK key. The raw key is forwarded straight into the encrypting store
    and never logged; if secret encryption is not configured, the store refuses rather than
    persisting plaintext (surfaced as 503)."""
    if body.kind is SecretKind.LLM_KEY and body.provider not in BYOK_PROVIDERS:
        raise ApiError(
            status_code=422,
            code="unsupported_provider",
            message=f"Unsupported provider {body.provider!r}.",
            retryable=False,
        )
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="secret_storage_unavailable",
            message="Secret encryption is not configured — cannot store a key.",
            retryable=False,
        ) from exc
    await SqlAlchemySecretStore(session, cipher).put(
        user_id=user.id, kind=body.kind, provider=body.provider, plaintext=body.key
    )


@router.delete("/me/keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    provider: str,
    kind: SecretKind = SecretKind.LLM_KEY,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """Remove a BYOK key. Idempotent — 204 whether or not a row existed (the store's own
    `delete` returns a bool we don't need to surface)."""
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return  # nothing could have been stored, so nothing to remove
    await SqlAlchemySecretStore(session, cipher).delete(
        user_id=user.id, kind=kind, provider=provider
    )


__all__ = ["router"]
