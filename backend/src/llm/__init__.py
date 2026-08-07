"""Provider-agnostic LLM adapter.

Depend on `llm.base.LLMClient` (a Protocol) everywhere outside `llm/registry.py` and the
provider-client modules themselves — never a concrete provider class.
"""

from __future__ import annotations

from llm.base import (
    LLMClient,
    LLMEvent,
    LLMResponse,
    Message,
    MessageRole,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEvent,
    ToolResultPart,
    ToolSpec,
)
from llm.registry import (
    ModelRole,
    ProviderNotConfiguredError,
    build_client,
    default_client,
    resolve_role,
)

__all__ = [
    "LLMClient",
    "LLMEvent",
    "LLMResponse",
    "Message",
    "MessageRole",
    "ModelCapabilities",
    "ModelRole",
    "ProviderNotConfiguredError",
    "StopReason",
    "StreamDoneEvent",
    "TextDeltaEvent",
    "TokenUsage",
    "ToolCall",
    "ToolCallDeltaEvent",
    "ToolCallEvent",
    "ToolResultPart",
    "ToolSpec",
    "build_client",
    "default_client",
    "resolve_role",
]
