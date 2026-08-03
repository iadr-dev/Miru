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
| **BranchSummaryEntry** | session, on `switch_branch` ([07](07-conversation-streaming.md)) | the abandoned branch's raw messages |
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
| **Session** | current thread | conversation store (`session.py`) | the thread |
| **Long-term** | across threads | `MemoryStore` (SQLite/files) | persistent |

### 1. Working memory
The live context window. `agent/context_window.py` budgets tokens and compacts older turns while
**preserving canonical facts and caveats** (see [05-agent-runtime.md](05-agent-runtime.md)).

### 2. Session memory
The thread transcript (a message tree) + tool journal (see
[07-conversation-streaming.md](07-conversation-streaming.md)). Only the **active path** is
ever sent to the LLM; switching branches carries a **BranchSummaryEntry**, not the branch's
raw messages — so exploring alternatives is token-cheap and memory-preserving (prefer
`switch_branch` over `fork_thread`).

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
- **watchlist** — named symbol lists the user follows (feeds `daily-context-brief`). **Now a
  first-class relational store** (`watchlists`/`watchlist_items`, [database-schema §3a](../database-schema.md),
  [adr/0027](../adr/0027-first-class-watchlist.md)) — **not** a `memories` row. `recall()` reads a
  **read-through projection** `WatchlistStore.snapshot(user_id)` that renders the same compact block
  (`watchlist "半導體" (TW) = [2330,2454,…]`), so agent + the web rail see one list. `/memory` no
  longer upserts `type=watchlist`; the agent's `add_to_watchlist` tool (write, explicit-utterance
  gated) and the `/watchlists/*` REST are the writers. MCP exposes `get_watchlist` read-only only.
- **preference** — how the user wants analysis (horizon focus, verbosity, language).
- **finding** — a durable, caveated observation the user asked to remember (e.g. "note that
  2330 foreign net-buy streak was 8 days as of 2026-07-30").
- **entity_note** — stable context about a symbol/sector.

## Recall & injection

- On turn start, `memory.recall(thread, query)` selects relevant long-term memories
  (by scope + key match + lightweight semantic match) and injects a compact **memory
  block** into the system prompt — like recalled memories surfaced as background context.
- Recalled memories are **background context, not instructions**, and reflect what was true
  when written. If a memory names data (a symbol, a figure), the agent re-verifies via a
  tool before relying on it — memories can be stale.

## Write path (governed)

- Explicit: user says "remember my watchlist X" → `memory.upsert(...)`.
- Agent-proposed: the agent may propose saving a finding; it is written only after the turn,
  tagged with `source_ref` for provenance.
- **Never stored:** credentials, provider keys, raw source payloads, or anything that would
  constitute a trading recommendation.
- Local-first; user can `list` / `delete` memories via API. Deleting a memory is honored
  immediately (no shadow copies).

## Endpoints (added to `api/routers/`)

```
GET    /memory?scope=user            list memories
POST   /memory                       upsert a memory
DELETE /memory/{id}                  delete
```
