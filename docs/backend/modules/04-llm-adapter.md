# Module: LLM adapter (`backend/src/llm/`)

Provider-agnostic `LLMClient` seam so callers (orchestrator, agent runtime, branch
summarization, analytics) depend on one Protocol and one normalized wire format, never a
concrete provider SDK. See [../adr/0001-provider-agnostic-llm.md](../adr/0001-provider-agnostic-llm.md).

Files: `base.py` (Protocol + wire types), `anthropic_client.py`, `openai_client.py`,
`gemini_client.py`, `openai_compat.py` (shared base for Groq/Cerebras/OpenRouter),
`local_client.py` (Ollama, delegates to `openai_compat.py`), `registry.py` (config-driven
construction + role routing). Tests: `backend/tests/llm/` (protocol conformance, one test
module per client, `registry.py`, plus `stubs.py`'s `StubLLMClient` used by every
runtime/orchestrator test that needs a deterministic, non-network `LLMClient`).

## Protocol (`base.py`)

`LLMClient` is a `@runtime_checkable Protocol` with three async members — `chat()`,
`stream()` (an `AsyncIterator[LLMEvent]`, not a `bool`-flag on `chat`), and `count_tokens()`
— plus `provider_id`/`model_id`/`capabilities` attributes. All wire types are frozen
Pydantic v2 models (`extra="forbid"`):

```python
class Message(BaseModel):
    role: MessageRole                          # SYSTEM | USER | ASSISTANT | TOOL
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResultPart, ...] = ()

class ToolCall(BaseModel):
    id: str; name: str; arguments: dict[str, object]

class LLMResponse(BaseModel):
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    stop_reason: StopReason                    # END_TURN | TOOL_USE | MAX_TOKENS | ERROR
    usage: TokenUsage                           # input_tokens/output_tokens/total_tokens

class ModelCapabilities(BaseModel):
    native_tool_calling: bool = True
    streaming: bool = True
    extended_thinking: bool = False
    native_web_search: bool = False

LLMEvent = TextDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent
```

`stream()` always terminates with exactly one `StreamDoneEvent` carrying the final
`stop_reason`/`usage`. Constructing any client performs **no network I/O** — only
`chat()`/`stream()`/`count_tokens()` do (every client's test module has a
`test_construction_performs_no_network_call`). `chat()`/`stream()` never swallow a
transport/auth error into `LLMResponse` — they log a warning (never the API key) and
re-raise; retry/fallback policy lives one layer up, in the callers below.

## Concrete clients — what's actually implemented

| File | Provider | `provider_id` | `count_tokens()` | Notes |
|------|----------|---------------|-------------------|-------|
| `anthropic_client.py` | Anthropic Claude (**default**) | `anthropic` | real SDK endpoint (`messages.count_tokens`) | `native_web_search=True`; uses `AsyncAnthropic` |
| `openai_client.py` | OpenAI | `openai` | exact local `tiktoken` count (`o200k_base` fallback for unregistered model ids) | `native_web_search=False` — Chat Completions has no general grounding tool for the catalog models; uses `AsyncOpenAI`, native SDK types (not the compat JSON path) |
| `gemini_client.py` | Google Gemini | `gemini` | real SDK endpoint (`aio.models.count_tokens`) | `native_web_search=True`; uses `google-genai`'s `Client(...).aio` async surface |
| `openai_compat.py` | Groq / Cerebras / OpenRouter | `groq` / `cerebras` / `openrouter` | heuristic, ~4 chars/token | one `OpenAICompatClient(provider_id, base_url, ...)` parameterized over `httpx`, shared because the three speak identical OpenAI chat-completions wire format |
| `local_client.py` | Ollama (local) | `ollama` | delegates to the compat heuristic | thin wrapper that constructs an `OpenAICompatClient` pointed at `{base_url}/v1`; no API key ever read from `Settings` |

Provider/model **ids** are config data, not a hardcoded enum in core — `registry.py` is the
only place that maps a `provider_id` string to a concrete class. There is no compiled-in
list of "supported model ids" anywhere in `llm/`; any `model_id` string is passed through to
the provider SDK/HTTP body as-is. (The original design doc's "supported catalog" table of
specific model ids per provider is **not built** — there is no such table in code; only the
four `ModelRole` suggested-default specs in `registry.py` name specific models, and those
are just defaults, fully overridable.)

Provider-specific normalization worth knowing when debugging a client:
- **Anthropic**: `system` messages collapse into the top-level `system` param (not a message
  in the list); assistant tool-call blocks and `tool_result` blocks are built per Anthropic's
  content-block shape; streaming assembles `input_json_delta` fragments per content-block
  index, emitting a `ToolCallEvent` per assembled call once the stream closes.
- **OpenAI**: one `ToolResultPart` per `role=tool` `Message` (multiple results become
  multiple messages upstream); streaming tool-call args accumulate by array index via the
  SDK's typed `ChoiceDeltaToolCall` chunks.
- **Gemini**: `system` messages become `system_instruction` in `GenerateContentConfig`, not a
  `Content` entry. Gemini's `FunctionResponse` needs the function **name**, not just an id,
  so `gemini_client.py` rebuilds a `tool_call_id -> name` map by scanning prior ASSISTANT
  `tool_calls` in the passed-in message list. Where the SDK gives no call id, one is
  synthesized (`f"{name}_{index}"`) since Gemini matches a result by name, not that id.
  Gemini's stream yields each function call whole (no incremental JSON assembly) — so a
  `ToolCallDeltaEvent` + `ToolCallEvent` are emitted back-to-back per call, not accumulated
  over multiple deltas like Anthropic/OpenAI.
- **OpenAI-compat (Groq/Cerebras/OpenRouter/Ollama)**: hand-rolled HTTP via `httpx` against
  `{base_url}/chat/completions`; SSE `data:` line framing for streaming, terminated by a
  literal `[DONE]` sentinel; `stream_options: {"include_usage": true}` requested so the final
  usage is real, not estimated, even on the streaming path.

**Native web search** is exposed only as the `ModelCapabilities.native_web_search` flag per
client (`True` for Anthropic/Gemini, `False` for OpenAI/Groq/Cerebras/OpenRouter/Ollama on
today's catalog). **Not built:** there is no `AITW_WEBSEARCH` setting, no Brave-backend
routing, and no code anywhere in `llm/` that actually invokes a provider's native search tool
or falls back to a `web_search` tool implementation — the flag exists for a future caller to
branch on, but nothing currently reads it. (See [01-sources.md](01-sources.md) for the real,
separately-implemented Brave source adapter, which is not wired to this flag.)

## Selection (`registry.py`)

`build_client(provider_id, model_id, settings=None)` is the single place that maps a
`(provider_id, model_id)` pair to a concrete `LLMClient`, reading the matching key off
`Settings`:

```python
ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY
    / OPENROUTER_API_KEY          # Settings fields anthropic_api_key, openai_api_key, ...
OLLAMA_BASE_URL   = http://localhost:11434     # settings.ollama_base_url, keyless
AITW_LLM_PROVIDER / AITW_LLM_MODEL             # default_client() override
```

`ollama` is the sole keyless provider. For every other `provider_id` in
`{anthropic, openai, gemini, groq, cerebras, openrouter}`, a missing key raises
`ProviderNotConfiguredError` (a `RuntimeError` subclass) — never crashes at import time, only
when that specific provider is actually requested. An unrecognized `provider_id` (anything
not in that set plus `ollama`) also raises `ProviderNotConfiguredError`, with a message
naming the id — a typo'd config value fails loudly instead of silently falling back to a
different vendor.

`default_client(settings=None)` builds the client for `AITW_LLM_PROVIDER`/`AITW_LLM_MODEL`,
falling back to the hardcoded default constants `_DEFAULT_PROVIDER = "anthropic"` /
`_DEFAULT_MODEL = "claude-sonnet-5"` when either is unset.

### Role-based model routing

`ModelRole` is a `StrEnum` with four members — `CLASSIFIER`, `AGENT`, `SUBAGENT`,
`SYNTHESIS` — matching the orchestrator's three-stage design
([11-orchestration.md](11-orchestration.md)). `resolve_role(role, settings=None)` maps a role
to a `"provider/model"` spec and constructs the client:

```python
AITW_MODEL_ROLE_CLASSIFIER   # settings.aitw_model_role_classifier, else "groq/llama-3.3-70b-versatile"
AITW_MODEL_ROLE_AGENT        # settings.aitw_model_role_agent,      else "anthropic/claude-sonnet-5"
AITW_MODEL_ROLE_SUBAGENT     # settings.aitw_model_role_subagent,   else "cerebras/gpt-oss-120b"
AITW_MODEL_ROLE_SYNTHESIS    # settings.aitw_model_role_synthesis,  else "anthropic/claude-opus-4-8"
```

Each override is a bare `"provider/model"` string (`_split_provider_model` raises `ValueError`
on a spec with no `/`). Pointing every `AITW_MODEL_ROLE_*` at `ollama/qwen3.6` is a pure
config change — no code path branches on role identity beyond this table lookup.

### Fallback — real mechanics, not a configured chain

The original design's **per-role ordered fallback list** (e.g.
`anthropic/claude-sonnet-5 → openai/gpt-5.4 → openrouter/auto`, configured in `config/`) is
**not built**. There is no fallback-chain config anywhere in `config/` or `llm/`, and
`registry.py` has no notion of "try the next provider in a list."

What's actually implemented is caller-level, hardcoded, and narrower:
- `orchestrator/orchestrator.py`'s `_resolve_subagent_client` catches
  `ProviderNotConfiguredError` from `resolve_role(ModelRole.SUBAGENT, ...)` and falls back to
  `resolve_role(ModelRole.AGENT, ...)` — one hop, subagent-role-unconfigured only.
- `orchestrator/classifier.py`'s `IntentClassifier` catches `ProviderNotConfiguredError` from
  `resolve_role(ModelRole.CLASSIFIER)` (provider unconfigured) and any other exception from
  the actual `chat()` call (transport/parse failure) and falls back to
  `_keyword_fallback()` — a deterministic keyword match over each skill's `triggers` — never
  to a different LLM provider.
- `orchestrator/synthesis.py` falls back the same way: `ProviderNotConfiguredError` on
  `resolve_role(ModelRole.SYNTHESIS)`, or any exception from the synthesis `chat()` call,
  degrades to `_structured_concatenation()` of the completed lane results — again, no
  cross-provider retry.

So today's "fallback chain" is: unconfigured/failing role → a same-process non-LLM
degradation specific to that caller (keyword match, plain concatenation, or the one
subagent→agent role hop) — never an automatic hop to a second *provider*. BYOK keys
([12-multi-user-tiers.md](12-multi-user-tiers.md)) exist as a concept in `accounts/secrets.py`
(`SecretStore.has_llm_key`, `SecretKind.LLM_KEY`) but nothing in `llm/registry.py` reads a
BYOK key — `build_client`/`resolve_role` only ever read the platform keys off `Settings`.
Wiring a user's BYOK key into `build_client` is **not built**.

## Callers (real usages outside `llm/`)

Grep for `llm.base`/`llm.registry` imports outside `llm/` turns up:

| Caller | What it uses |
|--------|--------------|
| `orchestrator/classifier.py` | `resolve_role(ModelRole.CLASSIFIER)` for the LLM router; `LLMClient`/`Message`/`MessageRole` to build the routing prompt |
| `orchestrator/orchestrator.py` | `resolve_role(ModelRole.AGENT \| SUBAGENT, settings)` per the tiering above |
| `orchestrator/synthesis.py` | `resolve_role(ModelRole.SYNTHESIS)` for final-answer synthesis |
| `agent/loop.py` | takes an injected `LLMClient` and calls `llm.chat(messages, tools=...)` inside the step loop (`step_start -> transform_context -> convert_to_llm -> llm.chat -> ...`) |
| `agent/context_window.py` | calls `llm.count_tokens(messages)` for context-window budgeting/compaction |
| `agent/hooks.py` | operates on `llm.base.Message` right before each `chat()` call |
| `conversation/models.py`, `conversation/store.py` | reuse `llm.base.ToolCall`/`ToolResultPart` field shapes for persisted turns; convert stored turns to `llm.base.Message` |
| `conversation/branch_summary.py` | calls `llm.chat(...)` with a cheap-role client (caller resolves the role, e.g. `ModelRole.CLASSIFIER`) to summarize a branch |
| `tools/base.py` | renders a `Tool`'s schema as `llm.base.ToolSpec` |
| `analytics/themes/research.py`, `analytics/themes/__main__.py` | `resolve_role(ModelRole.*)` / injected `LLMClient` for theme research (batch, not request-path) |

This confirms the orchestrator's model-tier routing ([11-orchestration.md](11-orchestration.md)
§4) is wired to this adapter exactly via `ModelRole` + `resolve_role` — the orchestrator never
constructs a provider client directly or names a provider/model literal itself.

## Testing (`backend/tests/llm/`)

- `stubs.py` — `StubLLMClient`, a scripted, non-network `LLMClient` (replays a fixed
  `Sequence[LLMResponse]`, records every `(messages, tools)` call) used across
  runtime/orchestrator tests per the project convention (`backend/CLAUDE.md` §7).
- `test_base.py` — Protocol-conformance + frozen-model checks.
- One test module per concrete client (`test_anthropic_client.py`, `test_openai_client.py`,
  `test_gemini_client.py`, `test_openai_compat.py`, `test_local_client.py`) — SDK/HTTP calls
  are mocked (`AsyncMock`, `httpx.MockTransport`), never real network, and every module
  asserts construction performs no network call and that a failed call never logs the API key.
- `test_registry.py` — `build_client` per provider (including the missing-key and
  unimplemented-provider error paths), `default_client` fallback/override, `resolve_role`
  including the offline-profile-to-Ollama case, and that construction alone never touches the
  network.
- `test_stub_client.py` — the stub itself (script replay, exhaustion, reset).

## Governance obligations

- No API keys persisted; platform keys are read from `Settings`/env at call time only. BYOK
  key decryption/injection into this layer is **not built** yet (see Fallback section above).
- The adapter transmits only what its caller passes in — normally the governed conversation
  package ([../governance.md](../governance.md)) — never raw source payloads; `llm/` has no
  visibility into `sources/`/`dataaccess/` by the one-way dependency rule.
- Every client's failure path logs `type(exc).__name__` only, never the exception's raw text
  or the API key — enforced by a dedicated `test_chat_never_logs_api_key_on_failure` in each
  client's test module.
- **Local/offline caveat:** `ollama` keeps inference on-device; every hosted provider receives
  whatever the caller sends and nothing more. Provider choice is a `Settings`/role-override
  value, never hardcoded, so an offline/privacy profile is achievable by config alone.
