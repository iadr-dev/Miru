# Module: Events, hooks & accounting (`backend/src/events/`, `backend/src/usage/`)

The cross-cutting layer that makes the agent observable, extensible, and safe: an internal
event bus, lifecycle hooks (the enforcement/automation seam), and usage/cost accounting +
rate limiting.

**Real module inventory** — `events/` has more real files than this doc's original scope
(`bus.py`, `hooks.py`, `rate_limit.py`) covered: `factory.py` (config-selected bus backend),
`redis_bus.py` (`RedisEventBus`/`RedisStreamReader`, the cross-worker transport),
`stream_store.py` (`SqlAlchemyStreamEventStore`, the durable `stream_events` replay log),
`data_updates.py` (the ingest-leader `data.updated` invalidation signal), `live_quote.py` +
`live_quote_factory.py` (the demand-driven live-quote poller/fan-out behind `/quote/stream`),
and `usage.py` (usage accounting, below). All are real and tested (`tests/events/`).

## 1. Event bus (`events/bus.py`, `events/factory.py`, `events/redis_bus.py`)

An async pub/sub, in-process by default. The agent loop, tools, and interfaces publish
lifecycle events; subscribers (SSE bridge, journal, metrics, hooks) consume them. The **SSE
protocol in [07-conversation-streaming.md](07-conversation-streaming.md) is one subscriber** —
the bus is the internal source of truth, SSE is its client-facing projection.

Two backends behind one `EventBusFactory` seam, config-selected
(`settings.event_bus_backend`), never hardcoded at a call site:
- **`memory`** (default) — `InMemoryEventBusFactory` hands out a fresh `InMemoryEventBus` per
  turn; single-process, no cross-worker reader (a reconnect can only live-attach to the
  in-process turn via the turn registry).
- **`redis`** — `RedisEventBusFactory` hands out a `RedisEventBus` per turn that mirrors every
  publish to the durable `stream_events` log (`SqlAlchemyStreamEventStore`) and a
  `stream:{turn_id}` Redis Pub/Sub channel, so a *different* worker can serve a reconnect via
  `RedisStreamReader` (durable replay-since, then live-follow).

Every event kind is defined once in `events/kinds.py` (`EventKind`, a `StrEnum`); `publish` takes an
`EventKind`, never a bare string. The kinds a chat turn streams to the web client are
`TURN_STREAM_KINDS` (`turn.start/end`, `route.decided`, `agent.spawned/progress/done`,
`synthesis.start`, `steering.accepted`, `rich_card`, `usage`, `tool.start`, `tool.result`,
`thinking.delta`, `web_search.result`, `memory.used`, `personality.used`, `pet.leveled`,
`pet.pull_earned`, `message`, `message.delta`); a test fails if that set and the web client's
`SSE_EVENT_KINDS` ever disagree. Other kinds are internal: `data.updated` (ingest invalidation,
`events/data_updates.py`) and the scheduler's `alert.fired` / `schedule.*`. Several are published via
the `events.hooks.EventBus*Hook` handlers below: `tool.start` fires the instant a tool call is about to
dispatch (before the governance veto, so it fires for a call that ends up blocked too), `tool.result`
(one kind, not a finer `proposed/started/locked/blocked` split), `thinking.delta` (a raw
extended-thinking chunk), `message.delta` (one already-governance-scanned sentence of the streaming
final answer) and `web_search.result` (one completed native-web-search call's `{agent_id, sources}`).
`activity`, `caveat`, `ask_user` and `follow_up` are not event kinds (`CAVEAT` is a
`conversation.models.MessageBlockType`).

```python
class Event(BaseModel):
    kind: str
    # correlation envelope — projected verbatim to the SSE frame ([07])
    seq: int                    # globally monotonic per turn
    ts: datetime
    turn_id: str | None = None
    agent_id: str | None = None       # "root" or a sub-agent id (parallel lanes)
    parent_agent_id: str | None = None
    node_id: str | None = None        # declared, not populated by any real publish call today
    data: dict[str, object]

class EventBus(Protocol):
    def publish(self, kind: str, *, turn_id=None, agent_id=None, parent_agent_id=None,
                node_id=None, data=None) -> Event: ...
    def subscribe(self, kinds: list[str] | None = None) -> AsyncIterator[Event]: ...
```

`publish` takes the event's fields directly (not a pre-built `Event`) and returns the `Event`
it assigned a monotonic `seq` to — the bus, not the caller, stamps `seq`/`ts`. The envelope
(`seq/agent_id/parent_agent_id/node_id`) is what lets the SSE bridge and the `stream_events`
table (`SqlAlchemyStreamEventStore`) rebuild the parallel-lane timeline
deterministically on reconnect — though `node_id` is a declared field with no real publisher
setting it yet.

## 2. Hooks — the enforcement seam (dependency-inverted)

Deterministic callbacks at fixed lifecycle points — the seam for audit, policy, and
automation (analogous to Claude Code hooks). The two governance points (`pre_tool`/
`pre_output`) can **veto**; the registrable extension points below (`pre_tool` observe-only/
`post_tool`/`pre_llm`/`thinking`/`answer_delta`/`web_search`) never veto — they observe/transform
only.

**Dependency inversion (preserves the one-way layer rule):** `agent/hooks.py` declares
`PreToolHook`/`PostToolHook`/`PreLLMHook`/`ThinkingHook`/`AnswerDeltaHook`/`WebSearchHook`/
`CancelToken`/`SteeringSource` as `Protocol`s in the runtime kernel (`agent/`), which is *below*
interaction/product. Higher layers **build concrete handlers** (`events/hooks.py::
EventBusPreToolHook`/`EventBusPostToolHook`/`EventBusThinkingHook`/`EventBusAnswerDeltaHook`/
`EventBusWebSearchHook`/`MemoryRecallPreLLMHook`) and pass them into `AgentLoop`'s constructor at
application-wiring time — `agent/loop.py` never imports `events/`. Note `PreToolHook` here is a
distinct, non-vetoing observe-only shape fired the instant a call is *about* to dispatch — not
the governance veto hook of the same informal name, which is `agent.guardrails.check_tool_call`
and stays a hardcoded call site (below). **`pre_tool` (governance)/`pre_output` are a different,
non-registrable shape**: they stay hardcoded call sites inside `agent/loop.py`
(`guardrails.check_tool_call`/`check_output`) — mandatory and not opened up for external
registration the way `post_tool`/`pre_llm`/`thinking`/`answer_delta`/`web_search` are.
Entitlement resolution is a third mechanism again: `accounts/hooks.py::EntitlementResolver`
resolves a `User` to a real-tier `ToolContext` **once per turn** (not a registered hook fired
per call), and `tools.base.dispatch_tool` re-applies its masking check on every dispatch using
whatever tier that `ToolContext` carries.

Two distinct enforcement concerns, cleanly separated (do not conflate):

| Hook point | Can veto? | **Governance** (`agent/guardrails.py`, hardcoded in `agent/loop.py`) | **Entitlement** (`tools.base.dispatch_tool`, tier resolved by `accounts/hooks.py`) | Other registered handlers |
|------------|-----------|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------|---------------------------|
| `pre_tool` (governance veto) | yes | `check_tool_call` — no-full-market-scan keyword/shape check | `dispatch_tool` — `sponsor` data-class → `MaskedTeaser` | — |
| `pre_tool` (observe-only) | no | — | — | `EventBusPreToolHook` — publishes a `tool.start` event the instant a call is about to dispatch (fires even for a call governance goes on to block), so a slow multi-tool Step shows something live instead of nothing until every call finishes |
| `post_tool` | no | — | — | `EventBusPostToolHook` — republishes a `tool.result` event (real; journal-writing itself is not a registered `post_tool` handler, it happens directly inside `agent/loop.py::_dispatch_one`) |
| `pre_output` | yes | `check_output` — trading-language redaction + caveat append | — | — |
| `pre_turn`/`post_turn` | pre: yes | — | `enforce_pre_turn_gate` (`api/turn_driver.py`) — daily-chat-cap + per-minute rate limit | `write_turn_usage` (post-turn durable usage write) |
| `pre_llm` | no (see below) | token-budget check (`agent.context_window.compact`, not registrable) | — | `MemoryRecallPreLLMHook` — injects the recalled-memory block; wired only on the `none`/`single` root-agent path (`orchestrator.orchestrator.Orchestrator._build_loop` attaches it when given a `recall_provider`, which `_run_none`/`_run_single` pass and `_run_subagent`/`_run_team`'s fan-out lanes never do) |
| `thinking` | no | — | — | `EventBusThinkingHook` — republishes each raw extended-thinking delta chunk as a `thinking.delta` event, `{agent_id, text}` |
| `answer_delta` | no | — | — | `EventBusAnswerDeltaHook` — republishes each already-governance-scanned sentence of the streaming final answer as a `message.delta` event, `{agent_id, text}`; `_build_loop` attaches it only for a `ModelRole.AGENT` loop (the root/synthesis answer), never a SUBAGENT-role fan-out lane, mirroring the `recall_provider` root-only carve-out above |
| `web_search` | no | — | — | `EventBusWebSearchHook` — republishes each completed native-web-search call as a `web_search.result` event, `{agent_id, sources}` |

- **Governance** (`guardrails.py`): non-trading, no-scan, caveats — a **runtime/core invariant**,
  hardcoded, not config-toggleable.
- **Entitlement**: paid-data licensing (`sponsor` masking) is `tools.base.dispatch_tool`, a
  **product concern**, separate from `guardrails.py`. Resolved **per tool call** in the sense
  that `dispatch_tool` re-checks `ctx.tier` on every dispatch — but that `tier` itself is
  resolved once per turn by `EntitlementResolver.tool_context_for`, not re-queried from the DB
  on each call.
- Both are **mandatory and cannot be disabled**; guardrail checks are the runtime form of the
  governance-as-code rules in [backend/CLAUDE.md](../../../backend/CLAUDE.md) §4.
- The `pre_llm` token-budget check (`context_window.compact`) is **not itself a vetoing hook** in
  `guardrails.py`'s sense — it runs unconditionally inside `AgentLoop._transform_context` before
  any registered `PreLLMHook` fires, and it rewrites the message list rather than refusing the
  call.

## 3. Usage & cost accounting (`events/usage.py`, `usage/store.py`)

- Per-turn counters are real: `UsageAccumulator` (constructed per turn in
  `orchestrator/orchestrator.py`) tracks input/output tokens, tool-call count, FinMind-call
  count, and cost-micros, and `.publish(bus)` emits the running snapshot as a live `usage`
  event. `write_turn_usage`/`SqlAlchemyUsageStore.record_turn` (`usage/store.py`) durably
  persist the finished snapshot to `usage_events`, idempotent on `(user_id, turn_id)`.
- **`TokenBreakdown`** (`system_prompt`/`recent_messages`/`compaction_summary`/
  `branch_summaries`/`memory_block`/`tool_schemas_*`/`tool_results`) is a typed model carried on
  `TurnUsage.breakdown`, but nothing populates it from the real context-composition sizes (there is no
  setter), so every field is `0` in practice. Per-model usage (`TurnUsage.models`) is populated.
- **Usage routes** (`api/routers/accounts_extra.py`): `GET /usage/quota` returns the caller's
  *resolved limits* (`EntitlementView`, from `accounts/entitlements.py`), `GET /usage/today` the
  chats claimed in today's UTC window, and `GET /usage/llm` a per-day / per-model summary of LLM usage
  metadata (`usage/llm_usage.py`). The per-turn `breakdown` is not exposed.

## 4. Rate limiting & quotas

- `pre_turn` hook enforces per-caller request/token budgets (config-driven) — protects
  provider spend and the FinMind Sponsor pool.
- **FinMind quota is not read from any live upstream endpoint** — the platform counts it
  locally. Every FinMind HTTP call, from the API and from ingest, is counted against the token's
  own cap (default **6000 per hour**, `finmind_calls_per_window`), with ingest limited to its share
  ([16](16-infrastructure.md) §4). On top of that, the orchestrator's lane pool
  (`RedisFinMindPoolReserver`, `usage/finmind_pool.py`) reserves lanes pre-flight before a
  Premium/Pro fan-out and only while calls are left; Free-tier turns skip the lane pool (their
  calls are still counted per call — there is one FinMind token, `finmind_api_key`). Approaching the
  ceiling degrades gracefully (`unavailable-tier` / an unavailable source), never crashes.

## Governance obligations
- Guardrail checks (`pre_tool`/`check_tool_call`, `pre_output`/`check_output`) are non-removable
  hardcoded call sites, not config-disable-able hooks.
- No event or accounting record persists credentials or raw payloads.
- Every tool dispatch outcome (blocked-before-dispatch, masked-teaser, or real) is journaled by
  `agent/loop.py::_dispatch_one` via `agent.journal.Journal.record_tool_call` — there is no
  `tool.locked` event kind; a masked `sponsor` result is journaled with
  `guardrail_reason="masked_teaser"` instead.
