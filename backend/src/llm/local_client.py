"""Ollama (local) client (`provider` id `ollama`).

No API key — `base_url` points at a local daemon (`OLLAMA_BASE_URL`, default
`http://localhost:11434`). Uses Ollama's OpenAI-compatible `/v1/chat/completions` endpoint
(documented, stable surface) rather than its native `/api/chat` — this lets us reuse the
exact same wire-format conversion helpers as `openai_compat.py` (Groq/Cerebras/OpenRouter),
proving the shared-base pattern generalizes even to a keyless local daemon (a deliberate
deviation from a literal one-file-per-provider split: implemented by thin composition over
`OpenAICompatClient` rather than a parallel hand-rolled converter, to avoid duplicating ~150
lines of normalization logic for identical wire semantics).

Construction never touches the network — only `chat()`/`stream()` hit the local daemon.
`count_tokens()` is a local heuristic (Ollama exposes no counting endpoint) — a reasonable
heuristic is an acceptable substitute here since there is no real endpoint to call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StreamDoneEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEvent,
    ToolSpec,
    WebSearchResultEvent,
)
from llm.openai_compat import OpenAICompatClient


class LocalOllamaClient:
    """The `ollama` `LLMClient` — local/offline/privacy option."""

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        capabilities: ModelCapabilities | None = None,
        timeout: float = 120.0,  # local inference on modest hardware can be slow
    ) -> None:
        self.provider_id = "ollama"
        self.model_id = model_id
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True, streaming=True, native_web_search=False
        )
        # No key: Ollama's OpenAI-compat endpoint accepts (and ignores) any bearer value.
        self._delegate = OpenAICompatClient(
            provider_id="ollama",
            model_id=model_id,
            base_url=f"{base_url.rstrip('/')}/v1",
            api_key="ollama-local-no-key-required",
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
        return await self._delegate.chat(messages, tools, native_web_search=native_web_search)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[
        TextDeltaEvent
        | ThinkingDeltaEvent
        | ToolCallDeltaEvent
        | ToolCallEvent
        | WebSearchResultEvent
        | StreamDoneEvent
    ]:
        return self._delegate.stream(messages, tools, native_web_search=native_web_search)

    async def count_tokens(self, messages: list[Message]) -> int:
        return await self._delegate.count_tokens(messages)


__all__ = ["LocalOllamaClient"]
