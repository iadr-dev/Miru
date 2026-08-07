"""`OpenAICompatClient` tests — recorded/mocked HTTP, no live network (default profile).

Uses `httpx.MockTransport` to replay a scripted response, same pattern as
`tests/sources/conftest.py`'s `make_json_client_factory` — adapted here because the client
constructs its own `httpx.AsyncClient(timeout=...)` per call rather than taking one via DI.
"""

from __future__ import annotations

import json

import httpx
import pytest

import llm.openai_compat as openai_compat_module
from llm.base import Message, MessageRole, StopReason, ToolResultPart, ToolSpec
from llm.openai_compat import OpenAICompatClient

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatch replaces it


def _client(**kwargs: object) -> OpenAICompatClient:
    defaults: dict[str, object] = {
        "provider_id": "groq",
        "model_id": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "test-key",
    }
    defaults.update(kwargs)
    return OpenAICompatClient(**defaults)  # type: ignore[arg-type]


def _patch_json_response(
    monkeypatch: pytest.MonkeyPatch, body: object, *, status_code: int = 200
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)


def _patch_sse_response(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    body = "\n".join(lines).encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)


def test_construction_performs_no_network_call() -> None:
    # No monkeypatch here — proves construction alone never opens a socket.
    client = _client()
    assert client.provider_id == "groq"
    assert client.model_id == "llama-3.3-70b-versatile"


async def test_chat_normalizes_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_json_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )
    client = _client()
    response = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert response.text == "hello"
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.total_tokens == 8


async def test_chat_normalizes_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_json_response(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "get_canonical_context",
                                    "arguments": json.dumps({"symbol": "2330"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )
    client = _client()
    response = await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_canonical_context", description="d", parameters={})],
    )

    assert response.text is None
    assert response.stop_reason is StopReason.TOOL_USE
    assert response.tool_calls[0].name == "get_canonical_context"
    assert response.tool_calls[0].arguments == {"symbol": "2330"}


async def test_chat_sends_tool_result_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            },
        )

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)

    client = _client()
    await client.chat(
        [
            Message(
                role=MessageRole.TOOL,
                tool_results=(ToolResultPart(tool_call_id="call_1", content="42"),),
            )
        ]
    )

    body = captured["body"]
    assert isinstance(body, dict)
    sent_messages = body["messages"]
    assert sent_messages[0] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


async def test_chat_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_json_response(monkeypatch, {"error": "bad request"}, status_code=400)
    client = _client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([Message(role=MessageRole.USER, content="hi")])


async def test_stream_assembles_text_and_tool_call_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        {"choices": [{"delta": {"content": "hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "get_x", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"a":1}'}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client()
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    text_events = [e for e in events if e.type == "text_delta"]
    assert "".join(e.text for e in text_events) == "hello"  # type: ignore[union-attr]

    tool_call_events = [e for e in events if e.type == "tool_call"]
    assert tool_call_events[0].tool_call.name == "get_x"  # type: ignore[union-attr]
    assert tool_call_events[0].tool_call.arguments == {"a": 1}  # type: ignore[union-attr]

    done = events[-1]
    assert done.type == "done"
    assert done.usage.total_tokens == 3  # type: ignore[union-attr]
    assert done.stop_reason is StopReason.TOOL_USE  # type: ignore[union-attr]


async def test_count_tokens_is_a_heuristic_estimate() -> None:
    client = _client()
    messages = [Message(role=MessageRole.USER, content="a" * 40)]
    # No network patched — proves count_tokens never calls out.
    assert await client.count_tokens(messages) == 10


async def test_count_tokens_never_below_one() -> None:
    client = _client()
    assert await client.count_tokens([]) == 1
