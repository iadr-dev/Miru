"""BYOK resolution: which provider/main/cheap model a keyed user's run uses, and the one
`byok_active` flag every caller shares."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from accounts.byok import ByokKeyRequiredError, byok_active, resolve_byok_override
from accounts.crypto import SecretCipher, generate_key
from accounts.models import SecretKind
from accounts.secrets import SqlAlchemySecretStore
from config.settings import Settings
from tests.accounts.conftest import make_user

KEYS = generate_key()


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, secret_encryption_keys=SecretStr(KEYS), **overrides)  # type: ignore[call-arg]


async def _save(db_session, user_id: str, *providers: str) -> None:
    store = SqlAlchemySecretStore(db_session, SecretCipher.from_settings(_settings()))
    for provider in providers:
        await store.put(
            user_id=user_id, kind=SecretKind.LLM_KEY, provider=provider, plaintext=f"sk-{provider}"
        )


async def test_no_keys_means_a_platform_run(db_session) -> None:
    user_id = await make_user(db_session)
    got = await resolve_byok_override(
        db_session, _settings(), user_id=user_id, model_override="anthropic/claude-opus-5-5"
    )
    assert got is None


async def test_default_is_the_first_provider_in_the_fixed_order(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "gemini", "openai", "openrouter")
    got = await resolve_byok_override(db_session, _settings(), user_id=user_id, model_override=None)
    assert got is not None
    assert (got.provider_id, got.main_model, got.cheap_model) == (
        "openai",
        "gpt-5.6-sol",
        "gpt-4o-mini",
    )
    assert got.api_key == "sk-openai"


async def test_the_platform_agent_role_config_is_never_consulted(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "gemini")
    settings = _settings(model_role_agent="anthropic/claude-opus-5-5")
    got = await resolve_byok_override(db_session, settings, user_id=user_id, model_override=None)
    assert got is not None
    assert got.provider_id == "gemini"


async def test_a_pick_for_a_keyed_provider_sets_main_and_that_providers_cheap(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "anthropic", "openai")
    got = await resolve_byok_override(
        db_session, _settings(), user_id=user_id, model_override="openai/gpt-5.4-mini"
    )
    assert got is not None
    assert (got.provider_id, got.main_model, got.cheap_model) == (
        "openai",
        "gpt-5.4-mini",
        "gpt-4o-mini",
    )


async def test_a_pick_without_a_key_for_its_provider_is_refused(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "openai")
    with pytest.raises(ByokKeyRequiredError) as exc:
        await resolve_byok_override(
            db_session, _settings(), user_id=user_id, model_override="anthropic/claude-opus-5-5"
        )
    assert exc.value.providers == ["openai"]


async def test_a_malformed_pick_is_ignored_in_favour_of_the_default(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "anthropic")
    got = await resolve_byok_override(db_session, _settings(), user_id=user_id, model_override="x")
    assert got is not None
    assert got.main_model == "claude-sonnet-5-5"


async def test_saved_default_model_applies_only_while_its_provider_has_a_key(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "anthropic", "gemini")
    got = await resolve_byok_override(
        db_session,
        _settings(),
        user_id=user_id,
        model_override=None,
        default_model="gemini/gemini-3.6-flash",
    )
    assert got is not None
    assert (got.provider_id, got.main_model) == ("gemini", "gemini-3.6-flash")
    # a stale default for a provider whose key was removed falls back to the provider default
    stale = await resolve_byok_override(
        db_session,
        _settings(),
        user_id=user_id,
        model_override=None,
        default_model="openai/gpt-5.6-sol",
    )
    assert stale is not None
    assert stale.provider_id == "anthropic"
    # an explicit pick beats the saved default
    picked = await resolve_byok_override(
        db_session,
        _settings(),
        user_id=user_id,
        model_override="anthropic/claude-opus-5-5",
        default_model="gemini/gemini-3.6-flash",
    )
    assert picked is not None
    assert picked.main_model == "claude-opus-5-5"


async def test_byok_active_is_key_presence_by_default(db_session) -> None:
    user_id = await make_user(db_session)
    assert await byok_active(db_session, _settings(), user_id=user_id) is False
    await _save(db_session, user_id, "openai")
    assert await byok_active(db_session, _settings(), user_id=user_id) is True


async def test_byok_active_without_encryption_config_is_false(db_session) -> None:
    user_id = await make_user(db_session)
    await _save(db_session, user_id, "openai")
    unconfigured = Settings(_env_file=None, secret_encryption_keys=None)  # type: ignore[call-arg]
    assert await byok_active(db_session, unconfigured, user_id=user_id) is False


async def test_byok_active_for_a_subscription_needs_the_flag_and_a_key(db_session) -> None:
    """The billing semantics: a BYOK subscription counts only while a key is on file, and a key
    alone never makes a non-BYOK subscription BYOK."""
    user_id = await make_user(db_session)
    settings = _settings()
    assert await byok_active(db_session, settings, user_id=user_id, subscription_byok=True) is False
    await _save(db_session, user_id, "openai")
    assert await byok_active(db_session, settings, user_id=user_id, subscription_byok=True) is True
    assert (
        await byok_active(db_session, settings, user_id=user_id, subscription_byok=False) is False
    )
