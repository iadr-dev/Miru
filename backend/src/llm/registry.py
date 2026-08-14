"""Provider/model selection from config/env only — never hardcoded (`registry.py`).

Three responsibilities:
1. `build_client(provider_id, model_id, settings)` — construct the right concrete
   `LLMClient` for a `(provider, model)` pair, reading the matching API key from `Settings`.
2. Role-based routing — the orchestrator (Phase 8,
   not yet built) names a **role**, not a model; `resolve_role(role, settings)` maps
   role -> an ordered chain of (provider, model) pairs from config and returns a client.
3. `FallbackLLMClient` — when a role's chain has more than one entry, wraps them so a failed
   `chat()`/`stream()` call (auth error, 429, outage) falls through to the next entry rather
   than failing the turn outright. A single-entry chain returns that entry's own client
   directly (no wrapper indirection for the common case).

No provider/model literal appears in *decision* logic here except the single documented
default constant (`_DEFAULT_PROVIDER`/`_DEFAULT_MODEL`) used only when nothing else is
configured — a default provider/model chosen when config is absent is data about which
entry of the supported catalog applies, not a hardcoded core decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Final

import structlog

from config.settings import Settings, get_settings
from llm.anthropic_client import AnthropicClient
from llm.base import LLMClient, LLMEvent, LLMResponse, Message, ModelCapabilities, ToolSpec
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
# `LLM_PROVIDER` / `LLM_MODEL`.
_DEFAULT_PROVIDER: Final[str] = "anthropic"
_DEFAULT_MODEL: Final[str] = "claude-sonnet-5"
_ANTHROPIC_SONNET_FALLBACK: Final[str] = "anthropic/claude-sonnet-4-6"

# Suggested per-role defaults, each an ordered fallback chain of "provider/model" specs — the
# first entry is tried first; on a `chat()`/`stream()` failure `FallbackLLMClient` tries the
# next. All overridable via `MODEL_ROLE_<ROLE>` = "<provider>/<model>[,<provider>/<model>...]".
_DEFAULT_ROLE_MODELS: Final[dict[ModelRole, tuple[str, ...]]] = {
    ModelRole.CLASSIFIER: (
        "groq/llama-3.3-70b-versatile",
        "cerebras/gpt-oss-120b",
        _ANTHROPIC_SONNET_FALLBACK,
    ),
    ModelRole.AGENT: (_ANTHROPIC_SONNET_FALLBACK, "anthropic/claude-haiku-4-5-20251001"),
    ModelRole.SUBAGENT: (_ANTHROPIC_SONNET_FALLBACK,),
    ModelRole.SYNTHESIS: ("openai/gpt-4o-mini",),
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


def _require_api_key(provider_id: str, settings: Settings) -> str:
    key = _api_key_for(provider_id, settings)
    if not key:
        raise ProviderNotConfiguredError(f"{provider_id}_api_key is not configured")
    return key


def _build_openai_compat_client(provider_id: str, model_id: str, settings: Settings) -> LLMClient:
    key = _require_api_key(provider_id, settings)
    # OpenRouter has its own native web-search plugin (`openai_compat._body` appends
    # `openrouter:web_search` when this capability is set) — Groq/Cerebras have no
    # equivalent, so they keep the default `ModelCapabilities()` (native_web_search=False).
    capabilities = (
        ModelCapabilities(native_tool_calling=True, streaming=True, native_web_search=True)
        if provider_id == "openrouter"
        else None
    )
    return OpenAICompatClient(
        provider_id=provider_id,
        model_id=model_id,
        base_url=_OPENAI_COMPAT_BASE_URLS[provider_id],
        api_key=key,
        capabilities=capabilities,
    )


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
        return AnthropicClient(model_id=model_id, api_key=_require_api_key(provider_id, settings))

    if provider_id == "openai":
        return OpenAIClient(model_id=model_id, api_key=_require_api_key(provider_id, settings))

    if provider_id == "gemini":
        return GeminiClient(model_id=model_id, api_key=_require_api_key(provider_id, settings))

    if provider_id in _OPENAI_COMPAT_BASE_URLS:
        return _build_openai_compat_client(provider_id, model_id, settings)

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
    """The client for `LLM_PROVIDER`/`LLM_MODEL`, falling back to the documented
    default (Anthropic/claude-sonnet-5) when unset."""
    settings = settings or get_settings()
    provider_id = settings.llm_provider or _DEFAULT_PROVIDER
    model_id = settings.llm_model or _DEFAULT_MODEL
    return build_client(provider_id, model_id, settings)


class FallbackLLMClient:
    """Wraps an ordered chain of `LLMClient`s so a role with more than one configured model
    degrades to the next entry on failure instead of failing the turn outright.

    Presents as the FIRST client in the chain (`provider_id`/`model_id`/`capabilities`) — every
    caller resolves a role once and treats the result as "the" client for that role, so this
    identity must be stable and representative of what a healthy call actually uses. Only
    `chat()`/`stream()` try the fallthrough; construction (this class's own `__init__`) never
    performs network I/O, matching every other `LLMClient`'s contract.
    """

    def __init__(self, clients: tuple[LLMClient, ...]) -> None:
        if not clients:
            raise ValueError("FallbackLLMClient needs at least one client")
        self._clients = clients
        self.provider_id = clients[0].provider_id
        self.model_id = clients[0].model_id
        self.capabilities = clients[0].capabilities

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for client in self._clients:
            try:
                return await client.chat(messages, tools, native_web_search=native_web_search)
            except Exception as exc:  # noqa: BLE001 — deliberately broad: any transport/auth/
                # rate-limit error from one provider must not abort the whole chain
                last_exc = exc
                logger.warning(
                    "llm_fallback_chat_failed",
                    provider=client.provider_id,
                    model=client.model_id,
                    error=str(exc),
                )
        assert last_exc is not None  # noqa: S101 — unreachable: __init__ guarantees clients is non-empty
        raise last_exc

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[LLMEvent]:
        last_exc: Exception | None = None
        for client in self._clients:
            try:
                # A generator's body doesn't run until first iterated, so a client that fails
                # immediately (bad auth) is only caught once we pull its first chunk — buffer
                # that one event rather than yielding straight from inside the try (a `yield`
                # inside `try`/`except Exception` would swallow the CONSUMER's own errors too).
                stream = client.stream(messages, tools, native_web_search=native_web_search)
                first = await stream.__anext__()
            except StopAsyncIteration:
                return
            except Exception as exc:  # noqa: BLE001 — see chat()'s own broad-catch rationale
                last_exc = exc
                logger.warning(
                    "llm_fallback_stream_failed",
                    provider=client.provider_id,
                    model=client.model_id,
                    error=str(exc),
                )
                continue
            yield first
            async for event in stream:
                yield event
            return
        assert last_exc is not None  # noqa: S101 — unreachable: __init__ guarantees clients is non-empty
        raise last_exc

    async def count_tokens(self, messages: list[Message]) -> int:
        return await self._clients[0].count_tokens(messages)


def _parse_role_chain(spec: str) -> tuple[str, ...]:
    """A role override is one or more comma-separated `provider/model` specs — the fallback
    chain, in try-order. Whitespace around each entry is stripped so `"a/b, c/d"` works."""
    return tuple(part.strip() for part in spec.split(",") if part.strip())


def resolve_role(role: ModelRole, settings: Settings | None = None) -> LLMClient:
    """Map a **role** (never a model) to its configured fallback chain of `(provider, model)`
    pairs and construct a client for it. An offline profile can point every role at
    `ollama/qwen3.6` purely via config — no code change. A single-entry chain returns that
    entry's client directly; a multi-entry chain returns a `FallbackLLMClient` that tries each
    in order on failure."""
    settings = settings or get_settings()
    role_overrides: dict[ModelRole, str | None] = {
        ModelRole.CLASSIFIER: settings.model_role_classifier,
        ModelRole.AGENT: settings.model_role_agent,
        ModelRole.SUBAGENT: settings.model_role_subagent,
        ModelRole.SYNTHESIS: settings.model_role_synthesis,
    }
    override = role_overrides[role]
    chain = _parse_role_chain(override) if override else _DEFAULT_ROLE_MODELS[role]

    # A single-entry chain must still raise `ProviderNotConfiguredError` loudly (a role with no
    # fallback has no other client to try). A multi-entry chain skips an unconfigured entry at
    # construction time too — the same graceful-degradation this class does at call time — and
    # only raises if EVERY entry in the chain is unconfigured.
    if len(chain) == 1:
        provider_id, model_id = _split_provider_model(chain[0])
        logger.debug("llm_role_resolved", role=role.value, provider=provider_id, model=model_id)
        return build_client(provider_id, model_id, settings)

    clients: list[LLMClient] = []
    for spec in chain:
        provider_id, model_id = _split_provider_model(spec)
        try:
            clients.append(build_client(provider_id, model_id, settings))
        except ProviderNotConfiguredError as exc:
            logger.debug(
                "llm_role_chain_entry_unconfigured",
                role=role.value,
                provider=provider_id,
                model=model_id,
                error=str(exc),
            )
            continue
        logger.debug("llm_role_resolved", role=role.value, provider=provider_id, model=model_id)

    if not clients:
        raise ProviderNotConfiguredError(
            f"no configured provider in {role.value}'s fallback chain: {chain!r}"
        )
    if len(clients) == 1:
        return clients[0]
    return FallbackLLMClient(tuple(clients))


__all__ = [
    "FallbackLLMClient",
    "ModelRole",
    "ProviderNotConfiguredError",
    "build_client",
    "default_client",
    "resolve_role",
]
