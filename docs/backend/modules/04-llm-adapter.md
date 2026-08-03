# Module: LLM adapter (`backend/src/llm/`)

Provider-agnostic abstraction so any of seven providers (Anthropic, OpenAI, Gemini, Groq,
Cerebras, OpenRouter, Ollama) can be swapped without touching the agent runtime. See
[../adr/0001-provider-agnostic-llm.md](../adr/0001-provider-agnostic-llm.md).

## Protocol (`base.py`)

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class LLMResponse(BaseModel):
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: TokenUsage

class LLMClient(Protocol):
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *, stream: bool = False,
    ) -> LLMResponse | Iterator[LLMEvent]: ...
    def count_tokens(self, messages: list[Message]) -> int: ...
```

The adapter **normalizes tool-call format** across providers so the runtime sees one
shape regardless of vendor.

## Concrete clients

Seven providers. Anthropic and Gemini have native clients; the rest are OpenAI
chat-completions-compatible and share one `openai_compat.py` base (different `base_url` +
key), which keeps the matrix cheap to extend. Model ids are supplied via config — the
table below is the **supported catalog**, not hardcoded in core.

| File | Provider | `provider` id | Models (`model` id) |
|------|----------|---------------|---------------------|
| `anthropic_client.py` | Anthropic Claude (**default**) | `anthropic` | `claude-sonnet-5`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-6`, `claude-haiku-4-5` |
| `openai_client.py` | OpenAI | `openai` | `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-4o` |
| `gemini_client.py` | Google Gemini | `gemini` | `gemini-3.1-pro-preview`, `gemini-3.5-flash` |
| `openai_compat.py` (base) | Groq | `groq` | `llama-3.3-70b-versatile` |
| `openai_compat.py` | Cerebras | `cerebras` | `gpt-oss-120b` |
| `openai_compat.py` | OpenRouter | `openrouter` | `auto` (meta-router) or any `vendor/model` |
| `local_client.py` | Ollama (local) | `ollama` | `qwen3.6` |

Notes:
- **OpenRouter** is a meta-provider: `openrouter/auto` lets it pick, or pass any explicit
  `vendor/model`; also usable as a universal fallback.
- **Groq / Cerebras** are fast/cheap inference of open models — good classifier/sub-agent tiers.
- **Ollama (`qwen3.6`)** is the local/offline/privacy option (no key, `base_url` = local daemon).
- **Native web search:** a `native_web_search` capability flag per model. When set (Anthropic
  `web_search` tool, OpenAI/Gemini grounding), the adapter can satisfy the `web_search` tool
  via the provider's own search; when unset (e.g. Ollama), the runtime routes `web_search` to
  the **Brave** backend ([01-sources.md](01-sources.md)). Either way the agent sees one
  `web_search` tool with a uniform result shape — provider difference is hidden here.
- Capability flags per model (native tool-calling, streaming, extended-thinking, native web
  search) live in a `model_capabilities` config map so the adapter degrades gracefully (e.g.
  prompt-based tool
  emulation where a model lacks native tools).

## Selection (`registry.py`)

Provider and model come from config/env only — no hardcoded provider in core:

```
AITW_LLM_PROVIDER = anthropic | openai | gemini | groq | cerebras | openrouter | ollama
AITW_LLM_MODEL    = <model id from the catalog above>
<PROVIDER>_API_KEY  = supplied at runtime, never stored   # e.g. ANTHROPIC_API_KEY, GROQ_API_KEY
OLLAMA_BASE_URL     = http://localhost:11434              # local, no key
OPENROUTER_API_KEY  = ...                                 # also used for the fallback router
BRAVE_API_KEY       = ...                                 # web-search fallback (provider-agnostic)
AITW_WEBSEARCH      = auto | brave | native | off         # auto = LLM-native if available else Brave
```

### Role-based model routing
The orchestrator ([11-orchestration.md](11-orchestration.md)) names a **role**, not a
model; the adapter maps role → provider/model from config. This enables cost/latency
tiering (cheap classifier + parallel sub-agents, best model for synthesis) without any
provider names in core logic. Suggested defaults (all overridable):

```
AITW_MODEL_ROLE_CLASSIFIER = groq/llama-3.3-70b-versatile     # fast + cheap, ~200 tokens
AITW_MODEL_ROLE_AGENT      = anthropic/claude-sonnet-5        # strong tool-use
AITW_MODEL_ROLE_SUBAGENT   = cerebras/gpt-oss-120b            # cheap parallel fan-out
AITW_MODEL_ROLE_SYNTHESIS  = anthropic/claude-opus-4-8        # best writing
# offline profile: every role → ollama/qwen3.6
```

### Fallback chains
Per-role ordered fallbacks on provider error / rate-limit (e.g.
`anthropic/claude-sonnet-5 → openai/gpt-5.4 → openrouter/auto`), configured in `config/`.
Free/local primaries never auto-escalate to a paid model without config opt-in. BYOK keys
([12-multi-user-tiers.md](12-multi-user-tiers.md)) override the platform key for that user.

## Governance obligations

- No API keys persisted; platform and BYOK keys are encrypted at rest and supplied at call
  time only (see [../governance.md](../governance.md)).
- The adapter transmits **only** the conversation package (Mode C) and governed context —
  never raw source payloads.
- **Local/offline caveat:** `ollama/qwen3.6` keeps data on-device; hosted providers receive
  the governed context only. Provider choice is surfaced so users know where inference runs.
