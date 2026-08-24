# Module: Memory (`backend/src/memory/`)

Three layers of memory so the agent is coherent within a turn, across a conversation, and
across sessions — without violating governance (no credentials, non-trading, local-first).

## Guiding principle — carry the conclusion, drop the transcript

Every layer applies the same rule: **compress lossily but preserve the decision.** Truncating
old messages is cheap but amnesiac; instead we keep *decisions + causes* and discard the raw
transcript. This shows up as three summary artifacts, all sharing one quality bar:

| Artifact | Where | Replaces |
|----------|-------|----------|
| **CompactionEntry** | working memory, linear history ([05](05-agent-runtime.md)) | old messages past `contextWindow − reserveTokens` |
| **BranchSummaryBlock** | session, on `switch_branch` ([07](07-conversation-streaming.md)) | the abandoned branch's raw messages — the summarization function is real/tested, but is not yet called from `switch_branch`/`fork` (see [07](07-conversation-streaming.md) §1) |
| **finding / entity_note** | long-term, cross-session | a whole prior analysis |

**Summary-quality requirement (make-or-break):** summaries must be **structured
decision+cause**, not prose — e.g. `{approach, outcome: tried|failed|confirmed, cause,
key_figures[], source_ref}`. A summary that drops *why* an approach failed lets the model
re-propose it. A cheap model is fine, but the summary prompt is a first-class, tested asset
(see Verification). Token savings are only valuable if the decision survives compression.

## Layers

| Layer | Scope | Backed by | Lifetime |
|-------|-------|-----------|----------|
| **Working** | current turn | context window (`agent/context_window.py`) | the turn |
| **Session** | current thread | `conversation.store.ConversationStore` (`conversation/store.py`) | the thread |
| **Long-term** | across threads | `memory.store.MemoryStore` (SQLAlchemy — SQLite dev / Postgres prod) | persistent |

### 1. Working memory
The live context window. `agent/context_window.py` budgets tokens and compacts older turns while
**preserving canonical facts and caveats** (see [05-agent-runtime.md](05-agent-runtime.md)).

### 2. Session memory
The thread transcript (a message tree) + the in-process per-Step audit trail (`agent/journal.py`
— see [07-conversation-streaming.md](07-conversation-streaming.md), which corrects the earlier
claim of a `journal_ref` field on `Turn`). Only the **active path** is ever sent to the LLM;
switching branches is *designed* to carry a **BranchSummaryBlock** rather than the branch's raw
messages, but that auto-summarization is not yet wired into `switch_branch`/`fork` — see
[07](07-conversation-streaming.md) §1's implementation-status note. Prefer `switch_branch` over
`fork_thread` regardless: it is still the only one of the two that never copies message rows.

### 3. Long-term memory
Durable facts the agent recalls in later sessions. Each memory is a small typed record:

```
Memory
 ├─ id, created_at, updated_at
 ├─ type: watchlist | preference | finding | entity_note
 ├─ scope: user | thread
 ├─ key, content        # e.g. watchlist "core" = [2330, 0050, 2454]
 └─ source_ref          # journal/turn that produced it (provenance)
```

Types:
- **watchlist** — named symbol lists the user follows. **Now a first-class relational store**
  (`watchlists`/`watchlist_items`, [database-schema §3a](../database-schema.md),
  [adr/0027](../adr/0027-first-class-watchlist.md)) — **not** a `memories` row. `recall()` reads a
  **read-through projection** `WatchlistStore.snapshot(user_id)` that renders the same compact
  block (`watchlist "半導體" (TW) = [2330,2454,…]`) — real, unit-tested
  (`tests/memory/test_watchlists.py`, `tests/memory/test_recall.py`) — and, per the recall-wiring
  status note above, now does feed a caller's watchlist into a live turn's prompt on the `none`/
  `single` root-agent path; it still reaches no prompt on a `subagent`/`team` fan-out lane, and the
  `daily-context-brief` built-in skill still takes an explicit `stock_ids` input rather than
  reading the watchlist store itself.
  `/memory` no longer upserts `type=watchlist`; only the `/watchlists/*` REST
  (`api/routers/memory.py`) writes it. ADR-0027's design for an agent-side `add_to_watchlist`
  write tool (explicit-utterance gated) and a read-only MCP `get_watchlist` tool is **not
  built** — neither exists in `tools/` or `mcp/` today; the agent has no tool-level watchlist
  access at all.
- **preference** — how the user wants analysis (horizon focus, verbosity, language).
- **finding** — a durable, caveated observation the user asked to remember (e.g. "note that
  2330 foreign net-buy streak was 8 days as of 2026-07-30").
- **entity_note** — stable context about a symbol/sector.

## Recall & injection

- `memory.recall.recall(memory_store, watchlist_store, user_id, thread_id, query)` selects
  relevant long-term memories (scope + a cheap token-overlap score against the query, plus the
  watchlist snapshot) and renders a compact **memory block** string — real and unit-tested
  (`tests/memory/test_recall.py`). `events.hooks.MemoryRecallPreLLMHook` is the `pre_llm`
  handler shape meant to inject that block into the message list right after compaction, and
  the loop-level dispatch point (`agent.loop.AgentLoop._run_pre_llm_hooks`) that would call it is
  real and unit-tested in isolation (`tests/events/test_hooks_registration.py`).
- **Implementation status — recall is wired into the live turn, scoped to the root `none`/
  `single` path only.** A `recall_provider` thunk is threaded from `api/turn_driver.py` through
  `Orchestrator.run` into `Orchestrator._build_loop`, which — only when it has a
  `recall_provider` — attaches `MemoryRecallPreLLMHook` as a `pre_llm` hook on the `AgentLoop` it
  builds. `_run_none`/`_run_single` (the `none`/`single` root-agent frameworks) pass
  `recall_provider` through; `_run_subagent`/`_run_team` → `_run_one_lane` never do, so a
  fan-out lane's `AgentLoop` never gets the hook and `recall()`'s output still reaches no prompt
  on those lanes. So a real `/agent/query`/`regenerate`/`edit` turn that routes to `none`/`single`
  now does see the recalled-memory block; one that routes to `subagent`/`team` does not.
- Recalled memories are meant to be **background context, not instructions**, and reflect what
  was true when written; the agent should re-verify any named data (a symbol, a figure) via a
  tool before relying on it — memories can be stale. This discipline is designed into
  `recall`'s rendered preface text, and now actually applies to the `none`/`single` root-agent
  path's live prompt per the status note above (still not to any fan-out lane's prompt).

## Write path (governed)

- **Explicit, real today:** the client calls `POST /memory {scope, type, key, content}` directly
  (`api/routers/memory.py`'s `upsert_memory`) — this is the only writer of
  `preference`/`finding`/`entity_note` memories; `type=watchlist` is refused here (§ above).
  There is no in-chat "remember my watchlist X" natural-language write path — the client/UI
  would have to call this endpoint itself; the agent has no tool that calls
  `MemoryStore.upsert`.
- **Agent-proposed saves are not built.** No tool in `tools/` calls into `memory.store`, and no
  orchestrator/loop code writes a `Memory` after a turn. The idea that "the agent may propose
  saving a finding, written only after the turn, tagged with `source_ref`" describes an intended
  future capability, not current behavior — today a finding/preference/entity_note is only
  written by a direct `POST /memory` call from outside the agent loop.
- Same gap on the watchlist side: ADR-0027 describes an `add_to_watchlist` agent tool (write,
  explicit-utterance gated) and a read-only `get_watchlist` MCP tool. Neither exists in
  `tools/` or `mcp/` today — watchlist reads/writes are exposed only as the `/watchlists/*` REST
  surface (`api/routers/memory.py`) and the read-through `WatchlistStore.snapshot()` projection
  `memory.recall.recall` renders (wired into the `none`/`single` root-agent path's prompt, not
  into any fan-out lane, per the status note above). The agent has no tool-level access to
  watchlists at all right now — the wiring above only injects a rendered snapshot as background
  context, it grants no callable tool.
- **Never stored:** credentials, provider keys, raw source payloads, or anything that would
  constitute a trading recommendation.
- Local-first; user can `list` / `delete` memories via API. Deleting a memory is honored
  immediately (no shadow copies).

## Endpoints (added to `api/routers/memory.py`)

```
GET    /memory?scope=user                              list memories
POST   /memory                                          upsert a memory (preference|finding|entity_note only)
DELETE /memory/{id}                                     delete

GET    /watchlists?market=                              list the caller's watchlist groups + items
POST   /watchlists                                       create a group
PATCH  /watchlists/{id}                                  rename and/or reorder (If-Match version CAS)
DELETE /watchlists/{id}                                  soft-delete a group
POST   /watchlists/reorder                               full-array group reorder
GET    /watchlists/{id}/items                            list a group's items
PUT    /watchlists/{id}/items/{symbol}                   star-add (idempotent)
DELETE /watchlists/{id}/items/{symbol}                   star-remove (idempotent, 204 either way)
PATCH  /watchlists/{id}/items/{item_id}                  set/clear an item note
POST   /watchlists/{id}/items/{item_id}/move             move an item to another group
```
