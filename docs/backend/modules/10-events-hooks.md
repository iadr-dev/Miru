# Module: Events, hooks & accounting (`backend/src/events/`, `backend/src/usage/`)

The cross-cutting layer that makes the agent observable, extensible, and safe: an internal
event bus, lifecycle hooks (the enforcement/automation seam), and usage/cost accounting +
rate limiting.

## 1. Event bus (`events.py`)

An in-process async pub/sub. The agent loop, tools, and interfaces publish lifecycle events;
subscribers (SSE bridge, journal, metrics, hooks) consume them. The **SSE protocol in
[07-conversation-streaming.md](07-conversation-streaming.md) is one subscriber** — the bus
is the internal source of truth, SSE is its client-facing projection.

Event kinds (superset of the SSE events, plus internal-only) — the full client-facing SSE set
([07](07-conversation-streaming.md)) plus internal kinds:
`turn.start/end`, `route.decided`, `agent.spawned/progress/done`, `synthesis.start`,
`steering.accepted`, `activity`, `message.delta`, `thinking.delta`,
`tool.proposed/started/result/locked/blocked`, `caveat`, `rich_card`, `ask_user`,
`follow_up`, `pet.reaction/leveled/pull_earned` (cosmetic, [21](21-pet-gamification.md)), `usage`
— plus internal-only: `memory.recalled/written`, `skill.loaded`, `source.fetch`, `error`.

```python
class Event(BaseModel):
    kind: str
    # correlation envelope — projected verbatim to the SSE frame ([07])
    seq: int                    # globally monotonic per turn
    ts: datetime
    turn_id: str | None
    agent_id: str | None        # "root" or a sub-agent id (parallel lanes)
    parent_agent_id: str | None
    node_id: str | None         # timeline node the event belongs to
    data: dict

class EventBus(Protocol):
    def publish(self, event: Event) -> None: ...
    def subscribe(self, kinds: list[str]) -> AsyncIterator[Event]: ...
```

The envelope (`seq/agent_id/parent_agent_id/node_id`) is what lets the SSE bridge and
`stream_events` table ([../database-schema.md](../database-schema.md)) rebuild the parallel-lane
timeline deterministically on reconnect.

## 2. Hooks — the enforcement seam (dependency-inverted)

Deterministic callbacks at fixed lifecycle points — the seam for audit, policy, and
automation (analogous to Claude Code hooks). Hooks run **in-process, synchronously**, and
can **veto** an action (unlike passive event subscribers).

**Dependency inversion (preserves the one-way layer rule):** the **`Hook` protocol + the
dispatch points live in the runtime kernel** (`agent/`), which is *below* interaction/product.
Higher layers **register handlers**; the runtime only knows the protocol, never the handler's
module. So `agent/loop.py` calling `pre_tool` does **not** create an upward dependency on
`events/`/`accounts/` — the runtime invokes registered `Hook`s abstractly. Entitlement data
the handlers need already arrives via `ToolContext` ([03-tools.md](03-tools.md)).

Two distinct enforcement concerns, cleanly separated (do not conflate — see governance §7):

| Hook point | Can veto? | **Governance** handler (`agent/guardrails.py`) | **Entitlement** handler (`accounts/entitlements.py`) | Other registered handlers |
|------------|-----------|-----------------------------------------------|------------------------------------------------------|---------------------------|
| `pre_tool` | yes | no-full-market-scan, arg validation | `sponsor` data-class → masked teaser | — |
| `post_tool` | no | — | — | journal, source-health, metrics |
| `pre_output` | yes | non-trading scan, caveat enforcement | — | — |
| `pre_turn`/`post_turn` | pre: yes | — | rate-limit / quota gate | usage finalization |
| `pre_llm` | yes | token-budget check | — | memory injection |

- **Governance** (`guardrails.py`): non-trading, no-scan, caveats — a **runtime/core invariant**.
- **Entitlement** (`accounts/entitlements.py`): paid-data licensing (`sponsor` masking, quota) —
  a **product concern**. It is a *separate mandatory hook*, not part of `guardrails.py`.
  Resolved **per tool call** (not cached for the whole turn), so a tier change mid-turn takes
  effect on the next call — no stale-entitlement window (§16 concurrency).
- Both are **mandatory and cannot be disabled**; guardrail hooks are the runtime form of
  [governance.md](../governance.md). Configured (order) via `config/`.

## 3. Usage & cost accounting

- Per-turn and per-thread counters: input/output tokens, tool calls, provider cost
  (from the LLM adapter's `TokenUsage`), FinMind API-call count.
- **Context-composition breakdown** per turn — tokens attributed to `system_prompt`,
  `recent_messages`, `compaction_summary`, `branch_summaries`, `memory_block`,
  `tool_schemas` (immediate vs deferred), `tool_results`. This makes the compaction /
  branch-summary / deferred-tool token savings **measurable**, and catches regressions where
  a summary silently balloons or a deferred tool set stops being deferred.
- Emitted as `usage` events (live) and persisted on the turn.
- `GET /usage?thread_id=...` and `GET /usage/summary` expose accounting (incl. the breakdown).

## 4. Rate limiting & quotas

- `pre_turn` hook enforces per-caller request/token budgets (config-driven) — protects
  provider spend and the FinMind Sponsor pool's static 6000 req/hr ceiling.
- FinMind quota is not read from any live upstream endpoint — the platform reserves its own
  budget locally: `RedisFinMindPoolReserver` (`usage/finmind_pool.py`) against a single static
  6000 req/hr ceiling shared across every Premium/Pro user, atomically reserved before each
  Sponsor-tier fetch and refunded on failure/under-use; surfaced via M5Q. Free tier never draws
  on this pool at all — there is no Free-tier FinMind quota concept. Approaching the limit
  degrades gracefully
  (`unavailable-tier` / cached), never crashes. A shared Sponsor token is pooled across users
  by the quota manager ([12-multi-user-tiers.md](12-multi-user-tiers.md)).

## Governance obligations
- Guardrail hooks (`pre_tool`, `pre_output`) are non-removable.
- No event or accounting record persists credentials or raw payloads.
- All hook vetoes (and tool dispatch / `tool.locked` outcomes) are journaled.
