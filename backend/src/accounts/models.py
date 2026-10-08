"""Domain models for accounts: the typed `User` record, its entitlement view, and the OAuth
provider enum. `Tier` itself lives in `schemas.enums` (project-wide vocabulary, already carried
on `ToolContext`); this module never redefines it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from schemas.enums import Tier


class OAuthProvider(StrEnum):
    GOOGLE = "google"
    LINE = "line"
    TELEGRAM = "telegram"


class SecretKind(StrEnum):
    """`user_secrets.kind` vocabulary. `LLM_KEY` is the BYOK case that lifts the chat limit."""

    LLM_KEY = "llm_key"
    FINMIND_TOKEN = "finmind_token"  # noqa: S105 — enum member value, not a hardcoded secret
    CHANNEL_TOKEN = "channel_token"  # noqa: S105 — enum member value, not a hardcoded secret
    ASR_KEY = "asr_key"
    TTS_KEY = "tts_key"


class User(BaseModel):
    """One authenticated user. `tier` is a derived cache (billing-webhook-owned in a later
    phase); `personality_id` is the selected agent-voice preset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    email: str | None = None
    display_name: str = ""
    picture_url: str | None = None
    birthday: str | None = None
    phone_country_code: str | None = None
    phone_number: str | None = None
    nationality: str | None = None
    tier: Tier = Tier.FREE
    personality_id: str = "steady"
    created_at: datetime
    updated_at: datetime


class OAuthAccount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    provider: OAuthProvider
    provider_account_id: str
    email_at_provider: str | None = None
    linked_at: datetime


class EntitlementView(BaseModel):
    """The resolved capability envelope for one user — what `GET /me` surfaces and what the
    API/agent consult. Purely a projection of `entitlements` over the user's tier + BYOK
    state; it holds no policy of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: Tier
    chat_limit_per_day: int | None  # None = unlimited (Pro, or BYOK)
    requests_per_minute: int
    finmind_class: Tier  # free | sponsor-equivalent — drives data_class unmask
    byok_active: bool
    features: tuple[str, ...]  # feature ids allowed at this tier
    # Models pickable on the platform key; None = unrestricted (paid tier, or BYOK).
    platform_models: tuple[str, ...] | None = None


__all__ = [
    "EntitlementView",
    "OAuthAccount",
    "OAuthProvider",
    "SecretKind",
    "User",
]
