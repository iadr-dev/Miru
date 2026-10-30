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

from collections.abc import AsyncIterator, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final

import structlog

from config.settings import Settings, get_settings, reveal
from llm.anthropic_client import AnthropicClient
from llm.base import LLMClient, LLMEvent, LLMResponse, Message, ModelCapabilities, ToolSpec
from llm.byok_profiles import BYOK_PROVIDER_TIERS
from llm.gemini_client import GeminiClient
from llm.local_client import LocalModelClient
from llm.openai_client import OpenAIClient
from llm.openai_compat import OpenAICompatClient
from llm.profiles import ModelRole, platform_role_chains

logger = structlog.get_logger(__name__)


# The default provider/model when nothing else is configured — Anthropic Claude is the
# documented default provider. Overridable via
# `LLM_PROVIDER` / `LLM_MODEL`.
_DEFAULT_PROVIDER: Final[str] = "anthropic"
_DEFAULT_MODEL: Final[str] = "claude-sonnet-5-5"
_OPENAI_COMPAT_BASE_URLS: Final[dict[str, str]] = {
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
    # `openrouter:web_search` when this capability is set); any other OpenAI-compatible provider
    # would keep the default `ModelCapabilities()` (native_web_search=False).
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

    Raises `ProviderNotConfiguredError` if the provider needs a key that isn't set (the local
    model is the sole keyless exception) — never raises at *import* time, and never performs
    network I/O itself (construction of the concrete client is likewise network-free).

    `api_key` is a user's own key (BYOK); when given it is used instead of the server's key.
    """
    settings = settings or get_settings()

    if provider_id == "local":
        return LocalModelClient(model_id=model_id, base_url=settings.local_llm_base_url)

    if provider_id == "anthropic":
        return AnthropicClient(
            model_id=model_id,
            api_key=_require_api_key(provider_id, settings, api_key),
            max_tokens=settings.llm_max_output_tokens,
            cache_diagnostics=settings.llm_cache_diagnostics,
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
    default (Anthropic/claude-sonnet-5-5) when unset."""
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

    def __init__(
        self,
        clients: tuple[LLMClient, ...],
        *,
        fall_through_on: Callable[[Exception], bool] | None = None,
    ) -> None:
        if not clients:
            raise ValueError("FallbackLLMClient needs at least one client")
        self._clients = clients
        # When given, only an error it accepts moves on to the next client; any other error
        # (e.g. a bad key) is raised as is. None means every error falls through.
        self._fall_through_on = fall_through_on
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
                if self._fall_through_on is not None and not self._fall_through_on(exc):
                    raise
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
                if self._fall_through_on is not None and not self._fall_through_on(exc):
                    raise
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


# Providers a user can bring their own key for (every one with a hosted, key-authenticated API).
BYOK_PROVIDERS: Final[frozenset[str]] = frozenset(BYOK_PROVIDER_TIERS)


@dataclass(frozen=True)
class ByokOverride:
    """One turn's "use the user's own key" instruction: `main_model` serves the reply agent, the
    sub-agent lanes and synthesis; `cheap_model` serves the classifier, titles and branch summaries.
    Both are bare model ids of `provider_id`."""

    provider_id: str
    api_key: str = field(repr=False)  # never printed or logged
    main_model: str
    cheap_model: str


_byok: ContextVar[ByokOverride | None] = ContextVar("byok_override", default=None)

# An HTTP status meaning "this model id is unknown / not allowed for this key". SDK errors expose it
# as `status_code` (Anthropic, OpenAI), `code` (Gemini) or on `.response` (httpx).
_MODEL_REJECTED_STATUSES: Final[frozenset[int]] = frozenset({400, 403, 404})


def _is_model_rejection(exc: Exception) -> bool:
    """Whether a provider rejected the model itself (unknown id, no access), as opposed to the key
    (401), a rate limit (429) or an outage - only the former degrades the cheap tier to main."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and status in _MODEL_REJECTED_STATUSES


@contextmanager
def byok_scope(override: ByokOverride | None) -> Generator[None]:
    """While active, every role resolution in this task uses the user's key - main model for the
    agent, sub-agents and synthesis, cheap model for the classifier - and never falls back to a
    platform key. The plaintext key lives only in this context for the duration of the turn."""
    token = _byok.set(override)
    try:
        yield
    finally:
        _byok.reset(token)


def assert_no_byok_scope() -> None:
    """Guard for platform-only jobs (research verification, theme builds, summary batches): they
    spend the platform's key and must never run inside a user's BYOK scope."""
    if _byok.get() is not None:
        raise RuntimeError(
            "platform-only job started inside a BYOK scope; it must not use a user key"
        )


def agent_model_spec(
    model_override: str | None, settings: Settings | None = None
) -> tuple[str, str]:
    """`(provider, model)` the interactive reply agent would use: the user's pick, else the
    AGENT role's first configured model."""
    settings = settings or get_settings()
    if model_override:
        return _split_provider_model(model_override)
    return _split_provider_model(platform_role_chains(settings)[ModelRole.AGENT][0])


def _byok_client(role: ModelRole, settings: Settings) -> LLMClient | None:
    """The BYOK client for `role`, or None outside a BYOK scope. The classifier role gets the cheap
    model; if the provider rejects that model it degrades to the main model on the same user key
    (never to a platform key). Every other role gets the main model."""
    active = _byok.get()
    if active is None:
        return None

    def build(model: str) -> LLMClient:
        return build_client(active.provider_id, model, settings, api_key=active.api_key)

    main = build(active.main_model)
    if role is not ModelRole.CLASSIFIER or active.cheap_model == active.main_model:
        return main
    try:
        cheap = build(active.cheap_model)
    except Exception as exc:  # the cheap tier is an optimization, never a reason to fail the turn
        logger.warning(
            "byok_cheap_model_unavailable_using_main",
            provider=active.provider_id,
            model=active.cheap_model,
            error=type(exc).__name__,
        )
        return main
    return FallbackLLMClient((cheap, main), fall_through_on=_is_model_rejection)


def resolve_role(role: ModelRole, settings: Settings | None = None) -> LLMClient:
    """Map a **role** (never a model) to its configured fallback chain of `(provider, model)`
    pairs and construct a client for it. An offline profile can point every role at
    `local/qwen3.6` purely via config — no code change. A single-entry chain returns that
    entry's client directly; a multi-entry chain returns a `FallbackLLMClient` that tries each
    in order on failure."""
    settings = settings or get_settings()
    if (byok_client := _byok_client(role, settings)) is not None:
        return byok_client
    chain = platform_role_chains(settings)[role]

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
    if (byok_client := _byok_client(role, settings)) is not None:
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
    "assert_no_byok_scope",
    "build_client",
    "byok_scope",
    "default_client",
    "resolve_role",
    "resolve_role_with_override",
]
