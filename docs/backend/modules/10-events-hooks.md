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

Event kinds actually emitted in the codebase today (grep-verified against every
`.publish(...)`/`bus.publish("...")` call site): `turn.start/end`, `route.decided`,
`agent.spawned/progress/done`, `synthesis.start`, `steering.accepted`, `rich_card`, `usage`,
`tool.result` (via `events.hooks.EventBusPostToolHook`, one kind — not the finer
`proposed/started/locked/blocked` split the original design named), `message` (the final-answer
frame, not `message.delta`), `data.updated` (ingest-invalidation, `events/data_updates.py`), and
the skill-load-time `skill_loaded` structlog line (not a bus event). **Not found anywhere in the
codebase:** `activity` (as a bus-published event kind — `"activity"` appears only as a data-field
key inside an `agent.spawned` event's payload), `message.delta`, `thinking.delta`, `caveat` (as
an event kind — `CAVEAT` is a `conversation.models.MessageBlockType`, not a bus event),
`ask_user`, `follow_up`, the `pet.*` kinds ([21](21-pet-gamification.md) publishes no bus
events), and `memory.recalled/written`/`source.fetch`. Treat this list as aspirational unless
grep-confirmed against a real `.publish(` call.

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
table ([../database-schema.md](../database-schema.md)) rebuild the parallel-lane timeline
deterministically on reconnect — though `node_id` is a declared field with no real publisher
setting it yet.

## 2. Hooks — the enforcement seam (dependency-inverted)

Deterministic callbacks at fixed lifecycle points — the seam for audit, policy, and
automation (analogous to Claude Code hooks). The two governance points (`pre_tool`/
`pre_output`) can **veto**; the two registrable extension points below (`post_tool`/`pre_llm`)
never veto — they observe/transform only.

**Dependency inversion (preserves the one-way layer rule):** `agent/hooks.py` declares
`PostToolHook`/`PreLLMHook`/`CancelToken`/`SteeringSource` as `Protocol`s in the runtime kernel
(`agent/`), which is *below* interaction/product. Higher layers **build concrete handlers**
(`events/hooks.py::EventBusPostToolHook`, `events/hooks.py::MemoryRecallPreLLMHook`) and pass
them into `AgentLoop`'s constructor at application-wiring time — `agent/loop.py` never imports
`events/`. **`pre_tool`/`pre_output` are a different, non-registrable shape**: they stay
hardcoded call sites inside `agent/loop.py` (`guardrails.check_tool_call`/`check_output`) —
mandatory and not opened up for external registration the way `post_tool`/`pre_llm` are.
Entitlement resolution is a third mechanism again: `accounts/hooks.py::EntitlementResolver`
resolves a `User` to a real-tier `ToolContext` **once per turn** (not a registered hook fired
per call), and `tools.base.dispatch_tool` re-applies its masking check on every dispatch using
whatever tier that `ToolContext` carries.

Two distinct enforcement concerns, cleanly separated (do not conflate — see governance §7):

| Hook point | Can veto? | **Governance** (`agent/guardrails.py`, hardcoded in `agent/loop.py`) | **Entitlement** (`tools.base.dispatch_tool`, tier resolved by `accounts/hooks.py`) | Other registered handlers |
|------------|-----------|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------|---------------------------|
| `pre_tool` | yes | `check_tool_call` — no-full-market-scan keyword/shape check | `dispatch_tool` — `sponsor` data-class → `MaskedTeaser` | — |
| `post_tool` | no | — | — | `EventBusPostToolHook` — republishes a `tool.result` event (real; journal-writing itself is not a registered `post_tool` handler, it happens directly inside `agent/loop.py::_dispatch_one`) |
| `pre_output` | yes | `check_output` — trading-language redaction + caveat append | — | — |
| `pre_turn`/`post_turn` | pre: yes | — | `enforce_pre_turn_gate` (`api/turn_driver.py`) — daily-chat-cap + per-minute rate limit | `write_turn_usage` (post-turn durable usage write) |
| `pre_llm` | no (see below) | token-budget check (`agent.context_window.compact`, not registrable) | — | `MemoryRecallPreLLMHook` — injects the recalled-memory block |

- **Governance** (`guardrails.py`): non-trading, no-scan, caveats — a **runtime/core invariant**,
  hardcoded, not config-toggleable.
- **Entitlement**: paid-data licensing (`sponsor` masking) is `tools.base.dispatch_tool`, a
  **product concern**, separate from `guardrails.py`. Resolved **per tool call** in the sense
  that `dispatch_tool` re-checks `ctx.tier` on every dispatch — but that `tier` itself is
  resolved once per turn by `EntitlementResolver.tool_context_for`, not re-queried from the DB
  on each call.
- Both are **mandatory and cannot be disabled**; guardrail checks are the runtime form of
  [governance.md](../governance.md).
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
- **`TokenBreakdown` (`system_prompt`/`recent_messages`/`compaction_summary`/
  `branch_summaries`/`memory_block`/`tool_schemas_immediate`/`tool_schemas_deferred`/
  `tool_results`) is a real typed model, but `UsageAccumulator.set_breakdown` is never called
  anywhere in the codebase** — no caller populates it from the real context-composition sizes,
  so every `TurnUsage.breakdown` field is `0` in practice today. The shape exists (per
  `events/usage.py`'s own docstring, "a future sink writes `.snapshot()` with no call-site
  change"), the wiring to actually measure and set it does not.
- **No `GET /usage` or `GET /usage/summary` endpoint exists.** The only real usage-facing route
  is `GET /usage/quota` (`api/routers/accounts_extra.py`), which returns the caller's *resolved
  limits* (`EntitlementView` — chats/day, req/min, FinMind class) from `accounts/entitlements.py`
  — it does not read `usage_events`/`usage_counters` or expose the breakdown; that route's own
  docstring says live consumption counters aren't surfaced there because no durable-rollup
  *read* path is wired yet (the write path above is real; nothing reads it back over HTTP).

## 4. Rate limiting & quotas

- `pre_turn` hook enforces per-caller request/token budgets (config-driven) — protects
  provider spend and the FinMind Sponsor pool.
- **FinMind quota is not read from any live upstream endpoint** — the platform reserves its own
  budget locally: a Redis-backed atomic counter (`RedisFinMindPoolReserver`, `usage/finmind_pool.py`)
  against a single static **6000 req/hr** ceiling shared across all Premium/Pro users, gated
  pre-flight before a fan-out spends Sponsor-tier tool calls and refunded on failure/under-use.
  Free tier never touches this pool (Free-tier skills use the free FinMind token). Approaching
  the ceiling degrades gracefully (`unavailable-tier` / cached), never crashes, and is surfaced
  via M5Q. A shared Sponsor token is pooled across users by the quota manager
  ([12-multi-user-tiers.md](12-multi-user-tiers.md)).

## Governance obligations
- Guardrail checks (`pre_tool`/`check_tool_call`, `pre_output`/`check_output`) are non-removable
  hardcoded call sites, not config-disable-able hooks.
- No event or accounting record persists credentials or raw payloads.
- Every tool dispatch outcome (blocked-before-dispatch, masked-teaser, or real) is journaled by
  `agent/loop.py::_dispatch_one` via `agent.journal.Journal.record_tool_call` — there is no
  `tool.locked` event kind; a masked `sponsor` result is journaled with
  `guardrail_reason="masked_teaser"` instead.
