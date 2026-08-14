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
from llm.base import ImagePart, Message, MessageRole, StopReason, ToolResultPart, ToolSpec
from llm.openai_compat import OpenAICompatClient, _message_to_wire

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

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)


def _patch_sse_response(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    body = "\n".join(lines).encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

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


def test_message_to_wire_sends_image_url_content_part_alongside_text() -> None:
    message = Message(
        role=MessageRole.USER,
        content="what's in this chart?",
        images=(ImagePart(media_type="image/png", data="Zm9v"),),
    )

    wire = _message_to_wire(message)

    assert wire["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zm9v"}},
        {"type": "text", "text": "what's in this chart?"},
    ]


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

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

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


async def test_chat_appends_openrouter_web_search_tool_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)

    client = _client(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    await client.chat([Message(role=MessageRole.USER, content="hi")], native_web_search=True)

    body = captured["body"]
    assert isinstance(body, dict)
    assert {"type": "openrouter:web_search"} in body["tools"]


async def test_chat_omits_openrouter_web_search_tool_when_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)

    client = _client(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    await client.chat([Message(role=MessageRole.USER, content="hi")])

    body = captured["body"]
    assert isinstance(body, dict)
    assert "tools" not in body


async def test_chat_never_appends_openrouter_tool_for_a_different_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    def factory(*, timeout: object = None, **_kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(openai_compat_module.httpx, "AsyncClient", factory)

    client = _client()  # provider_id="groq" — no native web-search mechanism
    await client.chat([Message(role=MessageRole.USER, content="hi")], native_web_search=True)

    body = captured["body"]
    assert isinstance(body, dict)
    assert "tools" not in body


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


async def test_stream_emits_thinking_delta_for_openrouter_reasoning_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter streams reasoning tokens as `delta.reasoning` (verified against OpenRouter's
    own published reasoning-tokens docs) — this must only fire for `provider_id ==
    "openrouter"`, mirroring the `native_web_search` provider carve-out already in this
    module."""
    chunks = [
        {"choices": [{"delta": {"reasoning": "pondering"}}]},
        {"choices": [{"delta": {"content": "final"}, "finish_reason": "stop"}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    thinking_events = [e for e in events if e.type == "thinking_delta"]
    assert [e.text for e in thinking_events] == ["pondering"]  # type: ignore[union-attr]

    text_events = [e for e in events if e.type == "text_delta"]
    assert [e.text for e in text_events] == ["final"]  # type: ignore[union-attr]


async def test_stream_emits_thinking_delta_for_openrouter_reasoning_content_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [{"choices": [{"delta": {"reasoning_content": "deep thought"}}]}]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    thinking_events = [e for e in events if e.type == "thinking_delta"]
    assert [e.text for e in thinking_events] == ["deep thought"]  # type: ignore[union-attr]


async def test_stream_never_emits_thinking_delta_for_a_non_openrouter_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Groq/Cerebras have no `reasoning`/`reasoning_content` field — even if a raw chunk somehow
    carried one, a non-OpenRouter provider must never surface it as a `thinking_delta`."""
    chunks = [{"choices": [{"delta": {"reasoning": "should be ignored", "content": "hi"}}]}]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client()  # provider_id="groq"
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    assert not [e for e in events if e.type == "thinking_delta"]
    text_events = [e for e in events if e.type == "text_delta"]
    assert [e.text for e in text_events] == ["hi"]  # type: ignore[union-attr]


async def test_stream_emits_web_search_result_event_for_openrouter_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter's `openrouter:web_search` streams sources as `delta.annotations`
    (`url_citation` objects) — verified live against a real streamed response (see the task's
    live-verification pass): one full annotation per chunk, accumulated across the whole
    stream and emitted as one `WebSearchResultEvent` right before `StreamDoneEvent`."""
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "url": "https://www.cnbc.com/quotes/NVDA",
                                    "title": "NVDA: NVIDIA Corp - Stock Price, Quote and News",
                                    "start_index": 0,
                                    "end_index": 0,
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "url": "https://stockanalysis.com/stocks/nvda/",
                                    "title": "NVIDIA (NVDA) Stock Price & Overview",
                                    "start_index": 0,
                                    "end_index": 0,
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"delta": {"content": "final"}, "finish_reason": "stop"}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    web_search_events = [e for e in events if e.type == "web_search_result"]
    assert len(web_search_events) == 1
    sources = web_search_events[0].sources  # type: ignore[union-attr]
    assert sources[0].url == "https://www.cnbc.com/quotes/NVDA"
    assert sources[1].url == "https://stockanalysis.com/stocks/nvda/"
    # emitted before the terminal `done` event
    assert events[-1].type == "done"
    assert events[-2].type == "web_search_result"


async def test_stream_never_emits_web_search_result_for_a_non_openrouter_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {"url": "https://x.com", "title": "X"},
                            }
                        ],
                        "content": "hi",
                    }
                }
            ]
        }
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    _patch_sse_response(monkeypatch, lines)

    client = _client()  # provider_id="groq"
    events = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    assert not [e for e in events if e.type == "web_search_result"]


async def test_count_tokens_is_a_heuristic_estimate() -> None:
    client = _client()
    messages = [Message(role=MessageRole.USER, content="a" * 40)]
    # No network patched — proves count_tokens never calls out.
    assert await client.count_tokens(messages) == 10


async def test_count_tokens_never_below_one() -> None:
    client = _client()
    assert await client.count_tokens([]) == 1
