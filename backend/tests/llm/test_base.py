"""Protocol-conformance tests for the normalized wire format (`llm/base.py`)."""

from __future__ import annotations

from llm.base import (
    ImagePart,
    LLMClient,
    LLMResponse,
    Message,
    MessageRole,
    ModelCapabilities,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolResultPart,
    ToolSpec,
)
from llm.local_client import LocalOllamaClient
from tests.llm.stubs import StubLLMClient, make_text_response


def test_message_round_trip_frozen_and_immutable() -> None:
    msg = Message(role=MessageRole.USER, content="hi")
    assert msg.content == "hi"
    assert msg.tool_calls == ()
    assert msg.tool_results == ()
    assert msg.images == ()


def test_message_can_carry_both_text_and_images() -> None:
    msg = Message(
        role=MessageRole.USER,
        content="what's in this chart?",
        images=(ImagePart(media_type="image/png", data="Zm9v"),),
    )
    assert msg.content == "what's in this chart?"
    assert msg.images[0].media_type == "image/png"


def test_tool_call_and_tool_result_shapes() -> None:
    call = ToolCall(id="1", name="get_x", arguments={"a": 1})
    assert call.arguments == {"a": 1}
    result = ToolResultPart(tool_call_id="1", content="ok")
    assert result.is_error is False


def test_llm_response_defaults_to_empty_tool_calls() -> None:
    response = LLMResponse(text="hi", stop_reason=StopReason.END_TURN, usage=_usage())
    assert response.tool_calls == ()


def test_tool_spec_carries_json_schema() -> None:
    spec = ToolSpec(
        name="get_canonical_context",
        description="fetch canonical context",
        parameters={"type": "object", "properties": {}},
    )
    assert spec.parameters["type"] == "object"


def test_model_capabilities_defaults() -> None:
    caps = ModelCapabilities()
    assert caps.native_tool_calling is True
    assert caps.native_web_search is False
    assert caps.supports_vision is False


def test_stub_client_satisfies_llmclient_protocol() -> None:
    """`StubLLMClient` (used by every future runtime/orchestrator test) must structurally
    satisfy the `LLMClient` Protocol — this is the seam contract, not an implementation
    detail."""
    stub = StubLLMClient(script=[make_text_response("hi")])
    assert isinstance(stub, LLMClient)


def test_local_ollama_client_satisfies_llmclient_protocol() -> None:
    client = LocalOllamaClient(model_id="qwen3.6", base_url="http://localhost:11434")
    assert isinstance(client, LLMClient)
    assert client.provider_id == "ollama"


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)
