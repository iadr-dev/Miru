"""`llm/profiles.py` - the platform model profile (hosted / local) and its precedence rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings
from llm.local_client import LocalModelClient
from llm.profiles import HOSTED_ROLE_MODELS, ModelRole, parse_role_chain, platform_role_chains
from llm.registry import FallbackLLMClient, _split_provider_model, agent_model_spec, resolve_role

# Frozen copy of the pre-profile defaults: the hosted profile must never drift from these.
_PREVIOUS_DEFAULTS = {
    ModelRole.CLASSIFIER: ("anthropic/claude-haiku-5-5", "anthropic/claude-sonnet-5-5"),
    ModelRole.AGENT: ("anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-5-5"),
    ModelRole.SUBAGENT: ("anthropic/claude-sonnet-5-5",),
    ModelRole.SYNTHESIS: ("openai/gpt-4o-mini", "anthropic/claude-sonnet-5-5"),
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type,call-arg]


def test_default_profile_is_hosted_and_matches_previous_defaults() -> None:
    settings = _settings()
    assert settings.platform_model_profile == "hosted"
    assert platform_role_chains(settings) == _PREVIOUS_DEFAULTS
    assert HOSTED_ROLE_MODELS == _PREVIOUS_DEFAULTS


def test_hosted_resolution_unchanged() -> None:
    settings = _settings(anthropic_api_key="sk-ant-test", openai_api_key="sk-test")
    assert isinstance(resolve_role(ModelRole.SYNTHESIS, settings), FallbackLLMClient)
    assert resolve_role(ModelRole.SUBAGENT, settings).provider_id == "anthropic"
    assert agent_model_spec(None, settings) == ("anthropic", "claude-sonnet-5-5")


def test_local_profile_maps_every_role_to_the_local_model() -> None:
    settings = _settings(platform_model_profile="local", local_model_id="ai/qwen3.6:latest")
    chains = platform_role_chains(settings)
    assert set(chains) == set(ModelRole)
    assert all(chain == ("local/ai/qwen3.6:latest",) for chain in chains.values())


def test_local_profile_resolves_to_local_model_without_any_hosted_key() -> None:
    settings = _settings(platform_model_profile="local", local_llm_base_url="http://x:1")
    for role in ModelRole:
        client = resolve_role(role, settings)
        assert isinstance(client, LocalModelClient)
        assert client.model_id == "ai/qwen3.6:latest"
    assert agent_model_spec(None, settings) == ("local", "ai/qwen3.6:latest")


def test_local_fallback_flag_appends_hosted_chain() -> None:
    settings = _settings(platform_model_profile="local", local_fallback_to_hosted=True)
    chains = platform_role_chains(settings)
    for role in ModelRole:
        assert chains[role] == ("local/ai/qwen3.6:latest", *_PREVIOUS_DEFAULTS[role])


def test_fallback_flag_is_ignored_by_the_hosted_profile() -> None:
    settings = _settings(local_fallback_to_hosted=True)
    assert platform_role_chains(settings) == _PREVIOUS_DEFAULTS


def test_model_role_override_beats_the_profile() -> None:
    settings = _settings(
        platform_model_profile="local",
        model_role_agent="anthropic/claude-haiku-5-5, openai/gpt-4o-mini",
    )
    chains = platform_role_chains(settings)
    assert chains[ModelRole.AGENT] == ("anthropic/claude-haiku-5-5", "openai/gpt-4o-mini")
    assert chains[ModelRole.CLASSIFIER] == ("local/ai/qwen3.6:latest",)


def test_model_role_override_beats_the_hosted_profile_too() -> None:
    settings = _settings(model_role_subagent="local/ai/qwen3.6:latest")
    chains = platform_role_chains(settings)
    assert chains[ModelRole.SUBAGENT] == ("local/ai/qwen3.6:latest",)
    assert chains[ModelRole.AGENT] == _PREVIOUS_DEFAULTS[ModelRole.AGENT]


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(platform_model_profile="cloud")


def test_default_local_model_spec_splits_on_the_first_slash_only() -> None:
    """`local/ai/qwen3.6:latest` carries an extra `/` and a `:`; only the first `/` separates
    the provider, and the `:` tag survives chain parsing."""
    settings = _settings(platform_model_profile="local")
    assert settings.local_model_id == "ai/qwen3.6:latest"
    (spec,) = platform_role_chains(settings)[ModelRole.AGENT]
    assert spec == "local/ai/qwen3.6:latest"
    assert parse_role_chain(spec) == (spec,)
    assert _split_provider_model(spec) == ("local", "ai/qwen3.6:latest")
