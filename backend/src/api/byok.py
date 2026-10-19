"""Bring-your-own-key: decide, before a turn starts, whether it runs on the caller's own key.

The rule (ADR: the user pays inference, so the daily chat cap does not apply): a caller with any
saved LLM key is a BYOK caller. Their turns run on THEIR key, on the model they picked, for every
LLM call in the turn - and never fall back to a platform key, which would be unmetered on our bill.
If they picked a model whose provider they have no key for, the turn is refused with a clear
message instead of silently spending ours.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from accounts.byok import (
    ByokKeyRequiredError,
    byok_providers,
    resolve_byok_override,
)
from api.errors import ApiError
from config.settings import Settings
from llm.byok_catalog import InvalidModelSpecError, UnknownModelError, parse_spec, validate_spec
from llm.registry import ByokOverride


def key_required(providers: list[str]) -> ApiError:
    return ApiError(
        status_code=409,
        code="byok_key_required",
        message=(
            "You've added your own API key for "
            f"{', '.join(providers)}. Pick a model from one of those providers, or remove "
            "your keys to use the shared models under your plan's limits."
        ),
        retryable=False,
    )


def invalid_model(spec: str, *, reason: str) -> ApiError:
    """The calm 422 for a pick that is not a catalog model (`reason` is user-readable)."""
    return ApiError(
        status_code=422,
        code="invalid_model",
        message=f"{spec!r} is not a model you can choose: {reason}",
        retryable=False,
    )


def check_catalog_spec(spec: str) -> str:
    """The normalized spec when it is a well-formed catalog model, else a 422 `ApiError`. Used for
    both per-request picks and the saved default; says nothing about the caller's keys."""
    try:
        provider, model = validate_spec(spec)
    except InvalidModelSpecError as exc:
        raise invalid_model(spec, reason="use the form provider/model.") from exc
    except UnknownModelError as exc:
        raise invalid_model(spec, reason="it is not in the model list.") from exc
    return f"{provider}/{model}"


async def validated_model_pick(
    session: AsyncSession, settings: Settings, *, user_id: str, pick: str | None
) -> str | None:
    """The model override to store/use for a caller's pick, or None for "no usable pick".

    - empty/None pick -> None;
    - a caller with NO saved key (a platform-profile caller) -> None: the platform profile decides
      the model, so the pick is ignored (never stored, never a 403);
    - a BYOK caller: malformed or unknown -> 422 `invalid_model`; a provider they hold no key for
      -> 409 `byok_key_required` (the same refusal a turn gives); else the normalized spec."""
    if pick is None or not pick.strip():
        return None
    providers = await byok_providers(session, settings, user_id=user_id)
    if not providers:
        return None
    try:
        provider, _ = parse_spec(pick)
    except InvalidModelSpecError as exc:
        raise invalid_model(pick, reason="use the form provider/model.") from exc
    if provider not in providers:
        raise key_required(providers)
    return check_catalog_spec(pick)


async def resolve_byok(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    model_override: str | None,
    default_model: str | None = None,
) -> ByokOverride | None:
    """None for a normal (platform-key) turn; a `ByokOverride` for a BYOK turn."""
    try:
        return await resolve_byok_override(
            session,
            settings,
            user_id=user_id,
            model_override=model_override,
            default_model=default_model,
        )
    except ByokKeyRequiredError as exc:
        raise key_required(exc.providers) from exc


__all__ = [
    "check_catalog_spec",
    "invalid_model",
    "key_required",
    "resolve_byok",
    "validated_model_pick",
]
