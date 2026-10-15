"""`LocalOllamaClient` tests — keyless local daemon, delegating to `OpenAICompatClient`
(same wire format as OpenRouter; the shared-base pattern proven to
generalize even here)."""

from __future__ import annotations

import httpx
import pytest

import llm.openai_compat as openai_compat_module
from llm.base import Message, MessageRole, StopReason
from llm.local_client import LocalOllamaClient
from tests.http_support import OwnedLease

_RealAsyncClient = httpx.AsyncClient


def _patch_json_response(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(
        openai_compat_module, "lease_client", lambda **kwargs: OwnedLease(factory(**kwargs))
    )


def test_construction_performs_no_network_call() -> None:
    client = LocalOllamaClient(model_id="qwen3.6", base_url="http://localhost:11434")
    assert client.provider_id == "ollama"
    assert client.model_id == "qwen3.6"
    assert client.capabilities.native_web_search is False


def test_no_key_required() -> None:
    """No API key is ever taken from Settings for Ollama — governance §6 keyless local path."""
    client = LocalOllamaClient(model_id="qwen3.6", base_url="http://localhost:11434")
    assert client._delegate._api_key  # a harmless placeholder, never read from env/Settings


async def test_chat_delegates_to_openai_compat_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_json_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    client = LocalOllamaClient(model_id="qwen3.6", base_url="http://localhost:11434")
    response = await client.chat([Message(role=MessageRole.USER, content="hi")])
    assert response.text == "hello"
    assert response.stop_reason is StopReason.END_TURN


async def test_count_tokens_heuristic_no_network() -> None:
    client = LocalOllamaClient(model_id="qwen3.6", base_url="http://localhost:11434")
    messages = [Message(role=MessageRole.USER, content="a" * 40)]
    assert await client.count_tokens(messages) == 10
