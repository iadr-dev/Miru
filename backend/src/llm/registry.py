"""Provider/model selection from config/env only — never hardcoded (`registry.py`).

Two responsibilities:
1. `build_client(provider_id, model_id, settings)` — construct the right concrete
   `LLMClient` for a `(provider, model)` pair, reading the matching API key from `Settings`.
2. Role-based routing — the orchestrator (Phase 8,
   not yet built) names a **role**, not a model; `resolve_role(role, settings)` maps
   role -> (provider, model) from config and returns the constructed client.

No provider/model literal appears in *decision* logic here except the single documented
default constant (`_DEFAULT_PROVIDER`/`_DEFAULT_MODEL`) used only when nothing else is
configured — a default provider/model chosen when config is absent is data about which
entry of the supported catalog applies, not a hardcoded core decision.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import structlog

from config.settings import Settings, get_settings
from llm.anthropic_client import AnthropicClient
from llm.base import LLMClient
from llm.gemini_client import GeminiClient
from llm.local_client import LocalOllamaClient
from llm.openai_client import OpenAIClient
from llm.openai_compat import OpenAICompatClient

logger = structlog.get_logger(__name__)


class ModelRole(StrEnum):
    """Cost/latency-tiered roles the orchestrator (Phase 8) names instead of a model."""

    CLASSIFIER = "classifier"
    AGENT = "agent"
    SUBAGENT = "subagent"
    SYNTHESIS = "synthesis"


# The default provider/model when nothing else is configured — Anthropic Claude is the
# documented default provider. Overridable via
# `AITW_LLM_PROVIDER` / `AITW_LLM_MODEL`.
_DEFAULT_PROVIDER: Final[str] = "anthropic"
_DEFAULT_MODEL: Final[str] = "claude-sonnet-5"

# Suggested per-role defaults, all overridable via
# `AITW_MODEL_ROLE_<ROLE>` = "<provider>/<model>".
_DEFAULT_ROLE_MODELS: Final[dict[ModelRole, str]] = {
    ModelRole.CLASSIFIER: "groq/llama-3.3-70b-versatile",
    ModelRole.AGENT: "anthropic/claude-sonnet-5",
    ModelRole.SUBAGENT: "cerebras/gpt-oss-120b",
    ModelRole.SYNTHESIS: "anthropic/claude-opus-4-8",
}

_OPENAI_COMPAT_BASE_URLS: Final[dict[str, str]] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class ProviderNotConfiguredError(RuntimeError):
    """Raised when the resolved provider has no API key configured (graceful degradation —
    the caller decides whether to fall back to another role/provider; the registry never
    crashes at import time, only at the point a specific provider is actually requested)."""


def _api_key_for(provider_id: str, settings: Settings) -> str | None:
    return {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "groq": settings.groq_api_key,
        "cerebras": settings.cerebras_api_key,
        "openrouter": settings.openrouter_api_key,
    }.get(provider_id)


def build_client(provider_id: str, model_id: str, settings: Settings | None = None) -> LLMClient:
    """Construct the concrete `LLMClient` for `(provider_id, model_id)`.

    Raises `ProviderNotConfiguredError` if the provider needs a key that isn't set (Ollama
    is the sole keyless exception) — never raises at *import* time, and never performs
    network I/O itself (construction of the concrete client is likewise network-free).
    """
    settings = settings or get_settings()

    if provider_id == "ollama":
        return LocalOllamaClient(model_id=model_id, base_url=settings.ollama_base_url)

    if provider_id == "anthropic":
        key = _api_key_for(provider_id, settings)
        if not key:
            raise ProviderNotConfiguredError("anthropic_api_key is not configured")
        return AnthropicClient(model_id=model_id, api_key=key)

    if provider_id == "openai":
        key = _api_key_for(provider_id, settings)
        if not key:
            raise ProviderNotConfiguredError("openai_api_key is not configured")
        return OpenAIClient(model_id=model_id, api_key=key)

    if provider_id == "gemini":
        key = _api_key_for(provider_id, settings)
        if not key:
            raise ProviderNotConfiguredError("gemini_api_key is not configured")
        return GeminiClient(model_id=model_id, api_key=key)

    if provider_id in _OPENAI_COMPAT_BASE_URLS:
        key = _api_key_for(provider_id, settings)
        if not key:
            raise ProviderNotConfiguredError(f"{provider_id}_api_key is not configured")
        return OpenAICompatClient(
            provider_id=provider_id,
            model_id=model_id,
            base_url=_OPENAI_COMPAT_BASE_URLS[provider_id],
            api_key=key,
        )

    # No client implementation for this provider id — a config that names one fails loudly
    # here rather than silently falling back to a different vendor.
    raise ProviderNotConfiguredError(
        f"provider {provider_id!r} has no LLMClient implementation in this build"
    )


def _split_provider_model(spec: str) -> tuple[str, str]:
    provider_id, _, model_id = spec.partition("/")
    if not model_id:
        raise ValueError(f"expected 'provider/model', got {spec!r}")
    return provider_id, model_id


def default_client(settings: Settings | None = None) -> LLMClient:
    """The client for `AITW_LLM_PROVIDER`/`AITW_LLM_MODEL`, falling back to the documented
    default (Anthropic/claude-sonnet-5) when unset."""
    settings = settings or get_settings()
    provider_id = settings.aitw_llm_provider or _DEFAULT_PROVIDER
    model_id = settings.aitw_llm_model or _DEFAULT_MODEL
    return build_client(provider_id, model_id, settings)


def resolve_role(role: ModelRole, settings: Settings | None = None) -> LLMClient:
    """Map a **role** (never a model) to its configured `(provider, model)` and construct
    the client. An offline profile can point every
    role at `ollama/qwen3.6` purely via config — no code change."""
    settings = settings or get_settings()
    role_overrides: dict[ModelRole, str | None] = {
        ModelRole.CLASSIFIER: settings.aitw_model_role_classifier,
        ModelRole.AGENT: settings.aitw_model_role_agent,
        ModelRole.SUBAGENT: settings.aitw_model_role_subagent,
        ModelRole.SYNTHESIS: settings.aitw_model_role_synthesis,
    }
    spec = role_overrides[role] or _DEFAULT_ROLE_MODELS[role]
    provider_id, model_id = _split_provider_model(spec)
    logger.debug("llm_role_resolved", role=role.value, provider=provider_id, model=model_id)
    return build_client(provider_id, model_id, settings)


__all__ = [
    "ModelRole",
    "ProviderNotConfiguredError",
    "build_client",
    "default_client",
    "resolve_role",
]
