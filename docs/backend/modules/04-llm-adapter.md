# Module: LLM adapter (`backend/src/llm/`)

Provider-agnostic `LLMClient` seam so callers (orchestrator, agent runtime, branch
summarization, analytics) depend on one Protocol and one normalized wire format, never a
concrete provider SDK. Why: models and vendors change faster than the product, so swapping or adding a provider must be a config change, not a caller change.

Files: `base.py` (Protocol + wire types), `anthropic_client.py`, `openai_client.py`,
`gemini_client.py`, `openai_compat.py` (shared base for OpenRouter),
`local_client.py` (Ollama, delegates to `openai_compat.py`), `registry.py` (config-driven
construction + role routing), `pricing.py` (list prices for estimated cost), `shared.py` (helpers common to the clients). Tests: `backend/tests/llm/` (protocol conformance, one test
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
| `openai_compat.py` | OpenRouter | `openrouter` | heuristic, ~4 chars/token | one `OpenAICompatClient(provider_id, base_url, ...)` parameterized over `httpx`, shared because the three speak identical OpenAI chat-completions wire format; `native_web_search=True` for OpenRouter only (its own web-search plugin), `False` for Groq/Cerebras |
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
- **OpenAI-compat (OpenRouter/Ollama)**: hand-rolled HTTP via `httpx` against
  `{base_url}/chat/completions`; SSE `data:` line framing for streaming, terminated by a
  literal `[DONE]` sentinel; `stream_options: {"include_usage": true}` requested so the final
  usage is real, not estimated, even on the streaming path.

**Native web search** is exposed as the `ModelCapabilities.native_web_search` flag per client
(`True` for Anthropic/Gemini/OpenRouter, `False` for OpenAI/Ollama on today's
catalog). Nothing inside `llm/` itself branches on this flag — the selection logic is one layer
up: `orchestrator/orchestrator.py::_maybe_add_web_search` reads a resolved client's
`capabilities.native_web_search` to decide, per turn, whether to grant `native_web_search=True`
into `chat()`/`stream()` or fall back to the Brave-backed `web_search`/`fetch_url` tools instead
(see [03-tools.md](03-tools.md)'s "Web search" section for the full mechanism). So the flag is
real and consumed today, just not by any code inside this module — `llm/` only declares it.

## Selection (`registry.py`)

`build_client(provider_id, model_id, settings=None)` is the single place that maps a
`(provider_id, model_id)` pair to a concrete `LLMClient`, reading the matching key off
`Settings`:

```python
ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY
    / OPENROUTER_API_KEY          # Settings fields anthropic_api_key, openai_api_key, ...
OLLAMA_BASE_URL   = http://localhost:11434     # settings.ollama_base_url, keyless
LLM_PROVIDER / LLM_MODEL                       # settings.llm_provider / settings.llm_model,
                                                # default_client() override
```

`ollama` is the sole keyless provider. For every other `provider_id` in
`{anthropic, openai, gemini, openrouter}`, a missing key raises
`ProviderNotConfiguredError` (a `RuntimeError` subclass) — never crashes at import time, only
when that specific provider is actually requested. An unrecognized `provider_id` (anything
not in that set plus `ollama`) also raises `ProviderNotConfiguredError`, with a message
naming the id — a typo'd config value fails loudly instead of silently falling back to a
different vendor.

`default_client(settings=None)` builds the client for `LLM_PROVIDER`/`LLM_MODEL`
(`settings.llm_provider`/`settings.llm_model`), falling back to the hardcoded default
constants `_DEFAULT_PROVIDER = "anthropic"` / `_DEFAULT_MODEL = "claude-sonnet-4-6"` when
either is unset.

### Role-based model routing

`ModelRole` is a `StrEnum` with four members — `CLASSIFIER`, `AGENT`, `SUBAGENT`,
`SYNTHESIS` — matching the orchestrator's three-stage design
([11-orchestration.md](11-orchestration.md)). `resolve_role(role, settings=None)` maps a role
to its configured **ordered fallback chain** of `"provider/model"` specs and constructs the
client (see Fallback below):

```python
MODEL_ROLE_CLASSIFIER   # settings.model_role_classifier, else the default chain:
                         #   "anthropic/claude-haiku-4-5-20251001" -> "anthropic/claude-sonnet-4-6"
MODEL_ROLE_AGENT        # settings.model_role_agent, else the default chain:
                         #   "anthropic/claude-sonnet-4-6" -> "anthropic/claude-haiku-4-5-20251001"
MODEL_ROLE_SUBAGENT     # settings.model_role_subagent, else the default chain:
                         #   "anthropic/claude-sonnet-4-6" (single entry, no fallback)
MODEL_ROLE_SYNTHESIS    # settings.model_role_synthesis, else the default chain:
                         #   "openai/gpt-4o-mini" (single entry, no fallback)
```

Each override is one or more comma-separated `"provider/model"` specs, in try-order
(`_parse_role_chain`; `_split_provider_model` raises `ValueError` on an individual spec with no
`/`). Pointing every `MODEL_ROLE_*` at `ollama/qwen3.6` is a pure config change — no code path
branches on role identity beyond this table lookup.

### Fallback — a real, ordered per-role provider chain

The per-role ordered fallback chain (e.g. `haiku -> sonnet` for
`CLASSIFIER`) is genuinely built, not just a future design intent. `_DEFAULT_ROLE_MODELS:
dict[ModelRole, tuple[str, ...]]` in `llm/registry.py` holds the default chain for each role
(quoted above); a `MODEL_ROLE_<ROLE>` setting overrides it with the caller's own
comma-separated chain. `resolve_role`:
- For a **single-entry chain**, builds and returns that one client directly (no wrapper) —
  and still raises `ProviderNotConfiguredError` loudly if that one provider has no key, since
  there is nothing else in the chain to try.
- For a **multi-entry chain**, builds a client for every entry it can (skipping — with a debug
  log, not an error — any entry whose provider is unconfigured) and wraps the survivors in
  `FallbackLLMClient`. It raises `ProviderNotConfiguredError` only if *every* entry in the
  chain is unconfigured.
- `FallbackLLMClient` (also in `registry.py`) presents as the **first** client in its chain for
  identity purposes (`provider_id`/`model_id`/`capabilities`), but `chat()`/`stream()` try each
  wrapped client in order, catching any exception (auth error, 429, transport failure, mid-
  stream break) and falling through to the next — only raising the last exception if every
  client in the chain fails. This is a genuine "try the next provider in a list" mechanism,
  living entirely in `registry.py`, not caller-side.

On top of that per-role chain, `orchestrator/orchestrator.py::Orchestrator._resolve_client`
(a single role-parameterized method — not a `_resolve_subagent_client`) adds one more,
narrower hop: for `ModelRole.SUBAGENT` specifically, it catches `ProviderNotConfiguredError`
from `resolve_role(ModelRole.SUBAGENT, ...)` (i.e. every entry in the SUBAGENT chain
unconfigured) and falls back to `resolve_role(ModelRole.AGENT, ...)` instead. `_resolve_client`
delegates the non-SUBAGENT (AGENT) path to `_resolve_agent_client`, which calls
`resolve_role_with_override` (see below) rather than `resolve_role` directly.

`orchestrator/classifier.py`'s `IntentClassifier` and `orchestrator/synthesis.py` add their own
non-LLM degradations on top of all the above: `IntentClassifier` catches
`ProviderNotConfiguredError` from `resolve_role(ModelRole.CLASSIFIER)` or any exception from
the actual `chat()` call and falls back to `_keyword_fallback()` (a deterministic keyword match
over each skill's `triggers`); `Synthesizer` degrades the same way on `ModelRole.SYNTHESIS`
failures, falling back to `_structured_concatenation()` of the completed lane results. Neither
of these is a provider hop — they're the last-resort floor below the provider chain above.

**Per-thread model override:** `llm/registry.py::resolve_role_with_override(role,
model_override, settings)` honors a user-supplied `"provider/model"` string for a role when one
is given, building that exact client; any failure to use it (malformed spec, unconfigured
provider, unimplemented provider) degrades to the role's normal configured chain via
`resolve_role` rather than failing the turn — a user's model pick is a preference, not a hard
requirement. `Orchestrator._resolve_agent_client` uses it for `ModelRole.AGENT`, and
`Synthesizer._resolve_client` uses it for `ModelRole.SYNTHESIS` — the two roles a thread-level
override is meaningful for; `CLASSIFIER`/`SUBAGENT` are cost/latency-tiered plumbing callers
never route a thread override through.

**BYOK** ([12-multi-user-tiers.md](12-multi-user-tiers.md)): `build_client(..., api_key=...)` takes a
user's own key, and `byok_scope(ByokOverride(provider_id, model_id, api_key))` is a context
variable that makes `resolve_role`/`resolve_role_with_override` return that one client for every
role (classifier, sub-agents and synthesis included) and never fall back to a platform key. The
override is built by `accounts/byok.py::resolve_byok_override` (decrypting the saved key) and
entered by `api/turn_driver.py`; `BYOK_PROVIDERS` lists the hosted providers a user may bring a
key for.

## Callers (real usages outside `llm/`)

Grep for `llm.base`/`llm.registry` imports outside `llm/` turns up:

| Caller | What it uses |
|--------|--------------|
| `orchestrator/classifier.py` | `resolve_role(ModelRole.CLASSIFIER)` for the LLM router; `LLMClient`/`Message`/`MessageRole` to build the routing prompt |
| `orchestrator/orchestrator.py` | `_resolve_client(role, model_override=...)` — `resolve_role(ModelRole.SUBAGENT \| AGENT, settings)` per the tiering above, with `resolve_role_with_override(ModelRole.AGENT, ...)` for the AGENT path's per-thread override |
| `orchestrator/synthesis.py` | `resolve_role_with_override(ModelRole.SYNTHESIS, ...)` for final-answer synthesis, honoring the same per-thread override mechanism |
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
- `test_byok_scope.py` — BYOK override resolution; `test_pricing.py` / `test_usage_parsing.py` — cost estimates and usage parsing.
- `test_registry.py` — `build_client` per provider (including the missing-key and
  unimplemented-provider error paths), `default_client` fallback/override, `resolve_role`
  including the offline-profile-to-Ollama case, and that construction alone never touches the
  network.
- `test_stub_client.py` — the stub itself (script replay, exhaustion, reset).

## Governance obligations

- No API keys persisted here; platform keys are read from `Settings`/env at call time only, and a
  BYOK key lives only in the turn's `byok_scope` context (`ByokOverride.api_key` is excluded from
  `repr`).
- The adapter transmits only what its caller passes in — normally the governed conversation
  package (rules: [../../../backend/CLAUDE.md](../../../backend/CLAUDE.md) §4 and §8) — never raw source payloads; `llm/` has no
  visibility into `sources/`/`dataaccess/` by the one-way dependency rule.
- Every client's failure path logs `type(exc).__name__` only, never the exception's raw text
  or the API key — enforced by a dedicated `test_chat_never_logs_api_key_on_failure` in each
  client's test module.
- **Local/offline caveat:** `ollama` keeps inference on-device; every hosted provider receives
  whatever the caller sends and nothing more. Provider choice is a `Settings`/role-override
  value, never hardcoded, so an offline/privacy profile is achievable by config alone.
