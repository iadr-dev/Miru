# Module: Agent runtime (`backend/src/agent/`)

The Claude Code-style loop, specialized for governed market-context analysis. Invoked by
the orchestrator ([11-orchestration.md](11-orchestration.md)) for `single`, `subagent`, and
`team` frameworks.

## `loop.py` — the agentic loop

**Domain vocabulary (single source: [07-conversation-streaming.md](07-conversation-streaming.md);
tables are the SQLAlchemy models in `backend/src/db/models/`):** a **Turn** = one user request → one agent
response cycle (the `turns` rows, `role=user|assistant`). The loop's inner unit — one LLM
response + the tool calls/results it triggers — is a **Step** (a.k.a. LLM round), **not** a
turn. There is no separate "Trace" entity; the hierarchy is `thread → turns → messages`.
Per **Step** (Pi-style two-stage context build):

```
agent_start → repeat one Step:
  step_start
    inject steering messages            # mid-run user input queued since last step
    transform_context(AgentMessage[])   # compaction only
    run pre_llm hooks                   # e.g. memory-block injection (registered handler)
    convert_to_llm(Message[])           # → provider-native payload (tool-call format)
    response = await llm.stream(payload, tools)  # genuinely streamed — AgentLoop._stream_step
                                                  # consumes text/thinking/tool-call/web-search
                                                  # deltas as they arrive, not one buffered call
    if response.tool_calls:
        for call: guardrails.check_tool_call → tools.base.dispatch_tool → post_tool hooks → append toolResult
    else: guardrails.check_output(text)  # non-trading, caveats
  step_end
  ├─ tool results still owed to LLM  → next Step
  ├─ pending steering message        → next Step
  ├─ pending follow-up               → next Step
  └─ none                            → agent_end (assistant Turn complete)
```

- **`transform_context`** (`AgentLoop._transform_context`) applies compaction only — it calls
  `agent.context_window.compact` and nothing else; system-prompt assembly happens separately at
  `run_turn`'s start (`assemble_system_prompt`), not inside `transform_context`. The **`pre_llm`
  hook point is a distinct step run right after** (`AgentLoop._run_pre_llm_hooks`, before
  `llm.stream`), on which **memory injection is a registered handler**
  (`events.hooks.MemoryRecallPreLLMHook`, [10](10-events-hooks.md) §2) — like entitlement is
  resolved per-turn, recall is layered in via the hook seam, not hardcoded in the kernel. Branch
  summaries are not built — no registered `pre_llm` handler for them exists today.
- **`convert_to_llm`** then maps internal messages → the active provider's payload/tool-call
  shape (the LLM adapter, [04](04-llm-adapter.md)) — the one place format differences live.
- Guardrails are mandatory hooks ([10-events-hooks.md](10-events-hooks.md)).

### Registrable, non-veto hooks (`agent/hooks.py`)

Besides the two mandatory, hardcoded governance hooks (`pre_tool`/`pre_output`, owned by
`agent.guardrails`), `agent/hooks.py` defines six observe-only Protocols a higher layer can
register into `AgentLoop`'s constructor without `agent/` ever importing that layer:

| Hook Protocol | Fires | Concrete handler (`events/hooks.py`) |
|---|---|---|
| `PreToolHook` | the instant a tool call is about to dispatch, before the governance veto even runs (so it fires for a call that ends up blocked too) | `EventBusPreToolHook` — publishes a `tool.start` event carrying just the tool name, so the frontend's process timeline has something to show live even when the model streams no thinking |
| `PostToolHook` | after every tool dispatch (blocked, masked, or real) | `EventBusPostToolHook` — publishes a `tool.result` event with a natural-language summary, argument keys, and the guardrail outcome (never the raw result) |
| `PreLLMHook` | on the message list right before every model call, after compaction | `MemoryRecallPreLLMHook` — injects the compact recalled-memory block as a system message once per turn |
| `ThinkingHook` | once per extended-thinking delta chunk streamed during a Step, already governance-scanned by the loop before the hook sees it | `EventBusThinkingHook` — republishes each chunk as a `thinking.delta` event, `{agent_id, text}` |
| `AnswerDeltaHook` | once per completed, already-governance-scanned sentence of the streaming final answer | `EventBusAnswerDeltaHook` — republishes each sentence as a `message.delta` event, `{agent_id, text}`, for token-by-token rendering |
| `WebSearchHook` | once per completed native-web-search call during a streamed Step | `EventBusWebSearchHook` — republishes the source list as a `web_search.result` event, `{agent_id, sources}` |

None of the six ever vetoes or removes content — they observe a dispatch point and, at most,
append/transform (`PreLLMHook` only). All six concrete handlers above are wired into every
`AgentLoop` build via `orchestrator/orchestrator.py::_build_loop`.

### Steering messages
If the user sends a message **while a turn is running**, it is queued as a *steering message*
via `POST /threads/{id}/steer` and injected at the next `step_start` — redirecting the agent
**without cancelling** the run (distinct from `/cancel`). Surfaced as an SSE `steering.accepted`
event (`api/routers/threads_actions.py`), not `message.delta`.

The consuming end is real: `agent/hooks.py` defines a `SteeringSource` Protocol (`drain() ->
tuple[str, ...]`, mirroring `CancelToken`'s own boundary discipline — polled only between Steps,
never mid-Step); `AgentLoop` holds an optional `steering_source` (constructor arg, also settable
post-construction via a property setter for wiring that builds the loop before the live turn
handle exists) and calls `_inject_steering` at the top of every `step_start`, before
`_transform_context` — draining whatever `POST .../steer` queued since the last Step and folding
it in as new user-role message(s), never merged into the system prompt (the governance rule that untrusted
content is data applies to a steering message exactly as it does to any other user turn).
The concrete `SteeringSource` is `api/turn_registry.py`'s `RunningTurn` — the same live handle the
steer endpoint appends to and `/cancel` flips — and `api/turn_driver.py` threads it from
`start_turn_stream`/`run_and_stream` into `Orchestrator.run()`.

**Scope: root lane only.** `steering_source` only redirects the `single`-framework root agent —
the one loop a turn-scoped client is actually attached to. A `subagent`/`team` fan-out's lanes are
short-lived, read-only sub-runs the client cannot individually address, so they are **not**
steerable mid-flight; a steering message sent during a fan-out is buffered and applied at
synthesis or the next root turn instead (never mutating an in-flight lane's context — no race).
This is a real scope limit, not a bug: a turn-only client has no per-lane handle to steer with.

## `planner.py`

Seeds an initial tool plan + step budget for a request. `Planner.plan_single` has exactly two
branches: if a skill-prescribed tool order is supplied, it becomes the plan (step budget =
`max(len(order) + 1, 6)`); otherwise it falls back to the **default warm plan** — the two
always-registered immediate tools it can find, `get_canonical_context` then `get_source_health`
(`_DEFAULT_WARM_TOOLS`) — with a flat step budget of 6 (`_DEFAULT_STEP_BUDGET`). There is no
`run_bounded_observation` proposal step or any other heuristic beyond that. **Not wired into any
real call site today** — `AgentLoop`/`Orchestrator` never construct or call `Planner`; it exists
as a standalone, independently tested module (`tests/agent/test_planner.py`) with no consumer
yet.

**Scope (vs orchestrator vs the LLM's own planning):** the orchestrator ([11](11-orchestration.md))
picks *framework + skills*; the ReAct loop + skill body order tool calls at run time. The
planner is a **thin, optional pre-pass for the `single` framework** that seeds an initial tool
plan + a step budget — it is *not* a second planning engine. If a skill body already prescribes
the tool order, the planner defers to it. (Kept minimal by design; not YAGNI only because it
supplies the step-count bound and a warm plan for skill-less `single` queries.)

## `context_window.py` — compaction

Token budgeting and transcript compaction (Pi-style). Trigger, computed from the newest
message backward:

```
contextTokens > contextWindow − reserveTokens   → compact
```

Compaction: keep the most recent segment verbatim (`_DEFAULT_KEEP_RECENT = 4` messages, widened
forward to the next valid boundary), replace the older segment with one **CompactionEntry**
(a `System`-role message), then rebuild context as `[leading system prompt] + compaction entry +
recent messages`. The summary itself is **not** LLM-generated — `_cheap_summary` is a flat
`"{N} earlier message(s) summarized to stay within the context budget."` line; `llm.count_tokens`
is only used to *decide whether* to compact, not to produce the summary text. Rules:
- **Split only at Assistant-Message boundaries** — **never between a Tool Call and its Tool
  Result** (they must stay paired or the provider rejects the payload).
- **Preserve canonical facts and caveats** through summarization: every compacted line
  containing a caveat/fact marker (`"caveat"`, `"currentness"`, `"canonical"`, `"fact:"`, etc.)
  is carried forward verbatim into `CompactionEntry.preserved_caveats`/`preserved_facts` — not
  lost to the cheap summary. Long-term recall/injection lives in [09-memory.md](09-memory.md).
- **No special-casing for a single over-long Turn.** The design's "summarize earlier history and
  the first half of the long turn separately" is not implemented — there is one split point per
  compaction pass, chosen purely by the boundary rule above, regardless of how much of the
  transcript belongs to one Turn.

## `guardrails.py` — governance enforcement (a mandatory hook)

Registered as a mandatory, hardcoded call site on `pre_tool`/`pre_output` inside `agent/loop.py`
(not a registrable handler like `PostToolHook`/`PreLLMHook` — see `agent/hooks.py`'s own
docstring on why the two governance checks stay hardcoded). Owns **governance only** — the
`sponsor` entitlement check is a *separate* mandatory gate, `tools.base.dispatch_tool` (wired
with the real per-user tier via `accounts/hooks.py::EntitlementResolver`), not part of
`guardrails.py`.

`agent/guardrails.py` implements exactly two checks, both keyword/pattern-based (not NLP):
- **`check_tool_call` (pre_tool, can veto):** rejects tool arguments that look like a
  full-market-scan request — a key name containing `scan_all`/`full_market`/`whole_market`/
  `market_wide`/`all_symbols`, a value of `*`/`all`/`market`, or a list argument naming more than
  20 symbols in one call.
- **`check_output` (pre_output, never vetoes — redacts/appends instead):** scans the candidate
  final text sentence-by-sentence for trading-language patterns (buy/sell/hold imperatives,
  price-target phrasing, position-sizing language, plus the Chinese 目標價/買進建議/賣出建議
  equivalents) and replaces an offending sentence with a fixed redaction notice; then, if the
  text draws on market data and doesn't already contain one of a fixed set of caveat markers
  (`"not financial advice"`, `"non-trading"`, `"currentness"`, …), appends the standard
  non-trading caveat sentence. `scan_structured_payload` is the same pattern-scan applied
  recursively to a rich card's structured fields (`agent/loop.py::_build_card`) instead of prose.

**Everything else the design lists under "guardrails" — source-provenance labeling of PTT/
social content, third-party analyst-estimate attribution, and prompt-injection defense for
fetched/searched content — is a *prompt-level* instruction in `agent/prompts/governance.py`'s
system prompt (rules 4–6), trusted to the model, not a second code-enforced check in
`guardrails.py`.** `check_output`'s keyword scan is the only backstop that actually runs code
against those rules, and only for the trading-language subset it pattern-matches — it does not
verify citations are present, does not distinguish PTT from any other source, and does not
detect an injection attempt as such.

Tested in `tests/agent/test_guardrails.py`; the governance-as-code rules live in
[`backend/CLAUDE.md`](../../../backend/CLAUDE.md) §4.

## Journal (`agent/journal.py`)

Each `JournalEntry` carries `agent_id`/`parent_agent_id` (same envelope as SSE, defaulting to
`"root"`/`None` for the single-lane case), so a fan-out is reconstructable **per lane** —
"which sub-agent produced this figure" is answerable, not a flat tool list. `Journal` is
in-memory only: `AgentLoop.run_turn` constructs one fresh `Journal` per turn and threads it
through `ToolContext.journal` and `_finalize_output`; nothing in `tools/` reads it back.

**Sink is structured logs, not a DB table.** `Journal.record_tool_call`/
`record_output_guardrail` append to an in-memory list *and* emit one `structlog` line
(`agent_step_tool_call_journaled`/`agent_step_output_guardrail_journaled`) per event — there is
**no `journal_entries` table** in `db/models/`; a DB-backed sink could plug into
`Journal.record_*` with no call-site change. Result
summaries never carry a tool's raw payload — `agent/loop.py::_summarize_result` reduces every
result to its type name plus its top-level field *names* (never values) before it reaches the
journal.

## Prompts (`agent/prompts/`)

Only two files exist: `agent/prompts/__init__.py` (the `PromptLayers` dataclass +
`assemble_system_prompt`/`default_prompt_layers`) and `agent/prompts/governance.py` (the
governance system-prompt text, `governance_prompt()`). The fixed-precedence assembly —
governance always first, then `personality`/`skill_body`/`pet_hint` layers appended only if
populated — is built; `default_prompt_layers()` populates only the governance layer, and the orchestrator
(`orchestrator.py::_skill_prompt_layers`) fills `personality`/`pet_hint`/`skill_body` per turn.

**Classifier and synthesis prompts are not here** — despite the design's original intent to
collect all role prompts under `agent/prompts/`, the classifier prompt
(`_ROUTER_SYSTEM_PROMPT`) and synthesis prompt (`_synthesis_system_prompt`) live in
`orchestrator/classifier.py` and `orchestrator/synthesis.py` respectively — they
are owned by the orchestrator layer, not `agent/`. There is no separate "compaction prompt"
either: `agent/context_window.py::compact` builds its `CompactionEntry` from a cheap, hardcoded
line-count summary (`_cheap_summary`), not a model-generated prompt/completion.
