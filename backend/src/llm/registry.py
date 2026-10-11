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

from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

import structlog

from config.settings import Settings, get_settings, reveal
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
_DEFAULT_MODEL: Final[str] = "claude-sonnet-4-6"
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
        "anthropic": reveal(settings.anthropic_api_key),
        "openai": reveal(settings.openai_api_key),
        "gemini": reveal(settings.gemini_api_key),
        "groq": reveal(settings.groq_api_key),
        "cerebras": reveal(settings.cerebras_api_key),
        "openrouter": reveal(settings.openrouter_api_key),
    }.get(provider_id)


def _require_api_key(provider_id: str, settings: Settings, api_key: str | None = None) -> str:
    key = api_key or _api_key_for(provider_id, settings)
    if not key:
        raise ProviderNotConfiguredError(f"{provider_id}_api_key is not configured")
    return key


def _build_openai_compat_client(
    provider_id: str, model_id: str, settings: Settings, api_key: str | None = None
) -> LLMClient:
    key = _require_api_key(provider_id, settings, api_key)
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


def build_client(
    provider_id: str,
    model_id: str,
    settings: Settings | None = None,
    *,
    api_key: str | None = None,
) -> LLMClient:
    """Construct the concrete `LLMClient` for `(provider_id, model_id)`.

    Raises `ProviderNotConfiguredError` if the provider needs a key that isn't set (Ollama
    is the sole keyless exception) — never raises at *import* time, and never performs
    network I/O itself (construction of the concrete client is likewise network-free).

    `api_key` is a user's own key (BYOK); when given it is used instead of the server's key.
    """
    settings = settings or get_settings()

    if provider_id == "ollama":
        return LocalOllamaClient(model_id=model_id, base_url=settings.ollama_base_url)

    if provider_id == "anthropic":
        return AnthropicClient(
            model_id=model_id,
            api_key=_require_api_key(provider_id, settings, api_key),
            max_tokens=settings.llm_max_output_tokens,
        )

    if provider_id == "openai":
        return OpenAIClient(
            model_id=model_id,
            api_key=_require_api_key(provider_id, settings, api_key),
            max_tokens=settings.llm_max_output_tokens,
        )

    if provider_id == "gemini":
        return GeminiClient(
            model_id=model_id,
            api_key=_require_api_key(provider_id, settings, api_key),
            max_output_tokens=settings.llm_max_output_tokens,
        )

    if provider_id in _OPENAI_COMPAT_BASE_URLS:
        return _build_openai_compat_client(provider_id, model_id, settings, api_key)

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
    default (Anthropic/claude-sonnet-4-6) when unset."""
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
            except Exception as exc:
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
            except Exception as exc:
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


# Providers a user can bring their own key for (every one with a hosted, key-authenticated API).
BYOK_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"anthropic", "openai", "gemini", "groq", "cerebras", "openrouter"}
)


@dataclass(frozen=True)
class ByokOverride:
    """One turn's "use the user's own model and key for every LLM call" instruction."""

    provider_id: str
    model_id: str
    api_key: str = field(repr=False)  # never printed or logged


_byok: ContextVar[ByokOverride | None] = ContextVar("byok_override", default=None)


@contextmanager
def byok_scope(override: ByokOverride | None) -> Generator[None]:
    """While active, every role resolution in this task uses the user's model and key - the
    classifier, sub-agents and synthesis included - and never falls back to a platform key. The
    plaintext key lives only in this context for the duration of the turn."""
    token = _byok.set(override)
    try:
        yield
    finally:
        _byok.reset(token)


def agent_model_spec(
    model_override: str | None, settings: Settings | None = None
) -> tuple[str, str]:
    """`(provider, model)` the interactive reply agent would use: the user's pick, else the
    AGENT role's first configured model."""
    settings = settings or get_settings()
    if model_override:
        return _split_provider_model(model_override)
    override = settings.model_role_agent
    chain = _parse_role_chain(override) if override else _DEFAULT_ROLE_MODELS[ModelRole.AGENT]
    return _split_provider_model(chain[0])


def _byok_client(settings: Settings) -> LLMClient | None:
    active = _byok.get()
    if active is None:
        return None
    return build_client(active.provider_id, active.model_id, settings, api_key=active.api_key)


def resolve_role(role: ModelRole, settings: Settings | None = None) -> LLMClient:
    """Map a **role** (never a model) to its configured fallback chain of `(provider, model)`
    pairs and construct a client for it. An offline profile can point every role at
    `ollama/qwen3.6` purely via config — no code change. A single-entry chain returns that
    entry's client directly; a multi-entry chain returns a `FallbackLLMClient` that tries each
    in order on failure."""
    settings = settings or get_settings()
    if (byok_client := _byok_client(settings)) is not None:
        return byok_client
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


def resolve_role_with_override(
    role: ModelRole, model_override: str | None, settings: Settings | None = None
) -> LLMClient:
    """`resolve_role(role, ...)`, but honoring a user-supplied `"provider/model"` override when
    one is given. Any failure to use it — malformed spec, unconfigured provider, or a provider
    with no `LLMClient` implementation — degrades to the role's normal configured chain rather
    than failing the turn; a user's model pick is a preference, not a hard requirement.

    Callers name which roles honor a thread's override — today that's `AGENT` (the interactive
    reply loop the user is actually talking to) and `SYNTHESIS` (the pass that writes the final
    answer a fan-out turn's user actually reads). `CLASSIFIER`/`SUBAGENT` are cost/latency-tiered
    plumbing the user has no mental model of and never named here, so callers simply don't pass
    an override through to them — this function itself does not enforce that carve-out."""
    settings = settings or get_settings()
    if (byok_client := _byok_client(settings)) is not None:
        return byok_client
    if model_override is None:
        return resolve_role(role, settings)
    try:
        provider_id, model_id = _split_provider_model(model_override)
        return build_client(provider_id, model_id, settings)
    except Exception as exc:  # malformed spec, unconfigured/unimplemented provider, etc.
        logger.warning(
            "model_override_failed_falling_back",
            role=role.value,
            model_override=model_override,
            error=str(exc),
        )
        return resolve_role(role, settings)


__all__ = [
    "BYOK_PROVIDERS",
    "ByokOverride",
    "FallbackLLMClient",
    "ModelRole",
    "ProviderNotConfiguredError",
    "agent_model_spec",
    "build_client",
    "byok_scope",
    "default_client",
    "resolve_role",
    "resolve_role_with_override",
]
