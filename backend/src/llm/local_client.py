"""Local model client (`provider` id `local`), served by Docker Model Runner.

No API key — `base_url` points at a local model server (`LOCAL_LLM_BASE_URL`, default
`http://localhost:12434/engines`). Uses its OpenAI-compatible `/v1/chat/completions` endpoint
(documented, stable surface) rather than its native `/api/chat` — this lets us reuse the
exact same wire-format conversion helpers as `openai_compat.py` (OpenRouter),
proving the shared-base pattern generalizes even to a keyless local daemon (a deliberate
deviation from a literal one-file-per-provider split: implemented by thin composition over
`OpenAICompatClient` rather than a parallel hand-rolled converter, to avoid duplicating ~150
lines of normalization logic for identical wire semantics).

Construction never touches the network — only `chat()`/`stream()` hit the local daemon.
`count_tokens()` is a local heuristic (the local server exposes no counting endpoint) — a reasonable
heuristic is an acceptable substitute here since there is no real endpoint to call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from llm.base import (
    LLMEvent,
    LLMResponse,
    Message,
    ModelCapabilities,
    ToolSpec,
)
from llm.openai_compat import TRANSPORT_ERRORS, OpenAICompatClient


class LocalModelUnavailableError(RuntimeError):
    """The local model endpoint could not be reached (daemon down, wrong address, timeout).

    Raised instead of the raw transport error so the API can answer with a calm, retryable 503
    and so nothing about the failure is mistaken for a hosted-provider problem."""

    def __init__(self, base_url: str) -> None:
        super().__init__("The local model is not reachable right now.")
        self.base_url = base_url


class LocalModelClient:
    """The `local` `LLMClient` — local/offline/privacy option."""

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        capabilities: ModelCapabilities | None = None,
        timeout: float = 120.0,  # local inference on modest hardware can be slow
    ) -> None:
        self.provider_id = "local"
        self.model_id = model_id
        self._base_url = base_url
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True, streaming=True, native_web_search=False
        )
        # No key: the local OpenAI-compat endpoint accepts (and ignores) any bearer value. This
        # is a placeholder sentinel, not a credential — nothing to rotate or protect.
        self._delegate = OpenAICompatClient(
            provider_id="local",
            model_id=model_id,
            base_url=f"{base_url.rstrip('/')}/v1",
            api_key="local-no-key-required",  # NOSONAR — not a real secret
            capabilities=self.capabilities,
            timeout=timeout,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        try:
            return await self._delegate.chat(messages, tools, native_web_search=native_web_search)
        except TRANSPORT_ERRORS as exc:
            raise LocalModelUnavailableError(self._base_url) from exc

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[LLMEvent]:
        try:
            async for event in self._delegate.stream(
                messages, tools, native_web_search=native_web_search
            ):
                yield event
        except TRANSPORT_ERRORS as exc:
            raise LocalModelUnavailableError(self._base_url) from exc

    async def count_tokens(self, messages: list[Message]) -> int:
        return await self._delegate.count_tokens(messages)


__all__ = ["LocalModelClient", "LocalModelUnavailableError"]
