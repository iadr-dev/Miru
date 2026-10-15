# Module: Conversation & streaming (`backend/src/conversation/`, `backend/src/api/`)

The chat backbone: persistent threads, a typed streaming event protocol, and turn lifecycle
(cancel/resume). This is what turns the agent loop into a real interactive chat backend.
There is **no approval handshake** — tools run freely; paid (`sponsor`) data simply streams
back as a masked teaser for unentitled users.

## 1. Conversation model

Messages form a **tree** (Pi-style): each message has `id` + `parent_id`, so a thread can
branch. The whole tree is persisted (JSONL/table); only the **active path** is sent to the LLM.

```
Thread            # a conversation (a message tree)
 ├─ id, title, created_at, updated_at, metadata (watchlist, provider/model)
 └─ Turn[]        # one user request → one agent response cycle (a Turn on the active path)
      ├─ id, role (user|assistant), status (running|done|cancelled|error), parent_turn_id
      ├─ Message[]   # ordered blocks: text, thinking, tool_use, tool_result, caveat, rich_card, steering,
      │              # branch_summary, compaction, attachment, web_search_sources (MessageBlockType)
      └─ usage        # tokens, tool calls, cost
```

### Persistence
- Store: `ConversationStore` protocol (`conversation/store.py`); SQLAlchemy async, SQLite dev
  default, Postgres in production. Tables are the SQLAlchemy models in `backend/src/db/models/conversation.py`.
- **No credentials persisted** (see [`backend/CLAUDE.md`](../../../backend/CLAUDE.md) §8) — only conversation content, tool
  journal, and derived context. The canonical package remains immutable and separate.
- Operations: `create_thread`, `list_threads`, `get_thread`, `append_turn`, `active_path`,
  `switch_branch(to_message)`, `fork_thread(from_turn)`, `delete_thread`, `update_thread`
  (rename/star/archive/tag with `expected_version` CAS), `get_turn`, `get_message`,
  `search_threads`.
- The per-Step audit trail (`tool_call`/`guardrail_output` entries, redacted result summaries)
  is `agent/journal.py`'s in-process `Journal` — a structured-log sink today, not a DB-backed
  `journal_ref` field on `Turn`; there is no such field on the `Turn`/`Message` models.

### Branch switching + Branch Summary (Pi `/tree`) — the token-efficient default
**Prefer `switch_branch` over `fork_thread` for exploring alternatives.** Because history is
one tree, switching branches does **not** copy or re-inflate context — only the active path
is ever sent to the LLM. The intended flow when the user switches from one branch to another
(e.g. C→E in `A─B─C \ D─E`): 1. find the two branches' common ancestor; 2. collect the
messages on the branch being left; 3. summarize them (cheap model, `conversation.branch_summary
.summarize_branch` — real and tested); 4. inject a **BranchSummaryBlock** at the target
position. So switching would keep the *conclusions* of the branch left behind ("tried approach
X → failed because …") at ~1% of the tokens — **less context, memory preserved**.

**Implementation status.** `summarize_branch` (the LLM-driven summarization step) and the
`BranchSummaryBlock` model are real and unit-tested (`tests/conversation/test_branch_summary
.py`). `ConversationStore.switch_branch` is a pure O(1) active-leaf pointer flip — it does
**not** call `summarize_branch` itself (by design: the store has no LLM handle). **Not yet
wired:** neither `POST /threads/{id}/switch-branch` nor `POST /threads/{id}/fork` actually
invokes `summarize_branch` today — both routers' own docstrings note this gap explicitly,
citing the missing cheap-role `LLMClient` handle at the interface layer. So a branch switch or
fork today carries **no** automatic summary of the abandoned branch; the pointer flip / empty
new-thread shell is real, the auto-summarization is a documented follow-up.

### `fork_thread` vs `switch_branch` — when to use which
| | `switch_branch` (default) | `fork_thread` (rare) |
|--|---------------------------|----------------------|
| Model | in-place tree, active-path swap | new thread |
| Context cost | **low** — only active path (no summary auto-injected yet) | new thread has no history at all (no summary auto-seeded yet) |
| Memory of other branch | none injected automatically today (see status note above) | none injected automatically today (see status note above) |
| Use when | "try another approach", edit-and-resend, compare | genuinely separate conversation you want to keep apart |

Fork's intended shape is a **reference to the shared tree + a summary of the source path** —
it must **not** deep-copy full history into the new thread (that was the naive, token-heavy
pattern). Same principle as compaction: carry the *summary*, not the raw transcript. Today
`ConversationStore.fork_thread` delivers only the "no deep copy" half: it creates an empty new
thread and copies no message rows — real and tested (`tests/conversation/test_store.py`) — but
does not yet seed that new thread's first turn with a `BranchSummaryBlock` of the source path
(see the status note above).

## 2. Typed streaming event protocol (SSE)

`POST /agent/query` and `GET /threads/{id}/stream` return `text/event-stream`. Every SSE
frame has an `id:` (monotonic per turn), an `event:` type, and JSON `data:`.

### Correlation envelope (every event carries it)
To render a **live timeline with parallel sub-agent lanes** (Claude Code-style), every event
carries a common envelope so the frontend can attribute each token/tool to the right lane:

```
{ seq, ts, turn_id, agent_id, parent_agent_id, node_id, ...payload }
```
- `agent_id` — which agent emitted it: `"root"` for the main loop, or a spawned sub-agent id.
- `parent_agent_id` — who spawned it (root's parent is null) → lets the client build the run tree.
- `node_id` — a stable id for the timeline node this event belongs to (a tool call, a message
  block, a sub-agent). Deltas for the same node share `node_id` so the UI appends in place.

### Event types
The kinds are defined once in `events/kinds.py` (`EventKind`; `TURN_STREAM_KINDS` must equal the
web client's `SSE_EVENT_KINDS`, enforced by `tests/events/test_event_kinds.py`). Payloads below are
in addition to the envelope.

| Event | Payload | Meaning |
|-------|---------|---------|
| `turn.start` | `{thread_id}` | turn accepted |
| `route.decided` | `{framework, skills, reason}` | orchestrator routing (see [11](11-orchestration.md)) |
| `agent.spawned` | `{agent_id, parent_agent_id, role, skill, activity}` | a sub-agent lane opened; `activity` is a templated one-liner (`Running {skill}`) |
| `agent.progress` | `{agent_id, status, phase}` | only `{status: "degraded", phase: "quota"}` is published today |
| `agent.done` | `{agent_id, status}` | sub-agent lane closed |
| `steering.accepted` | `{message}` | mid-run user message queued; `AgentLoop._inject_steering` folds it in at the next Step boundary (single-framework root lane only; proven by `tests/api/test_steering_wiring.py`) |
| `message.delta` | `{agent_id, text}` | streamed answer, one already-governance-scanned sentence per event; root/synthesis lane only |
| `thinking.delta` | `{agent_id, text}` | raw reasoning chunk, already governance-scanned; clients keep it collapsed by default |
| `tool.start` | `{step_index, tool_name, summary}` | a tool call is about to dispatch, published *before* the governance veto (fires for a call that ends up blocked too); a live preview of the later `tool.result` |
| `tool.result` | `{step_index, tool_name, argument_keys, summary, guardrail_allowed, guardrail_reason}` | tool returned (raw payload withheld); a masked `sponsor` call carries `guardrail_reason: "masked_teaser"` |
| `web_search.result` | `{agent_id, sources}` | sources of a native web-search call (`[{url, title}]`) |
| `rich_card` | `{card_type, data}` | structured UI card (stock card, comparison table, chart, gauge); emitted on the `single`, `subagent` and `team` frameworks, governed by a structured non-trading scan, never built from a masked tool result ([03-tools.md](03-tools.md): tools opt in via `card_type`). Interactive kinds: `ask_user` (option tap) and `alert_proposal` (`propose_alert` tool; the confirm button makes the *frontend* call `POST /alerts` - the agent never creates the rule) |
| `synthesis.start` | `{from_agents:[...]}` | main agent begins merging sub-agent results |
| `memory.used` | `{memories:[...]}` | long-term memories recalled for this turn ([09-memory.md](09-memory.md)) |
| `personality.used` | `{personality}` | tone preset applied to this turn |
| `pet.leveled` | `{pet_id, level}` | equipped pet leveled up (cosmetic, [21](21-pet-gamification.md)) |
| `pet.pull_earned` | `{count}` | a milestone granted gacha pull(s) (cosmetic) |
| `usage` | `TurnUsage` snapshot (tokens, `tool_calls`, `finmind_calls`, `cost_micros`, per-model breakdown) | published once per turn by `Orchestrator._finalize` |
| `message` | `{text, framework}` | the one final-answer frame, published just before `turn.end`; not chunked |
| `turn.end` | `{status}` (+ `message` on `error`) | `done`, `cancelled` or `error` |

**Not events.** There is no `caveat`, `activity`, `tool.proposed`, `tool.started`, `tool.locked`,
`tool.blocked`, `ask_user`, `follow_up`, `pet.reaction` or standalone `error` kind (verified against
`EventKind`): the non-trading caveat is inside the final `message` text (appended by
`agent.guardrails.check_output`), a masked or blocked call surfaces as a `tool.result`, and a
failed/cancelled turn publishes `turn.end` with `status` `"error"`/`"cancelled"`. The `ask_user`
*tool* is real (`tools/ask_user_tools.py`, `card_type="ask_user"`) and renders through `rich_card`.

**Live streaming.** `AgentLoop._stream_step` drives every Step via `llm.stream()` (never
`llm.chat()`). `EventBusThinkingHook` / `EventBusAnswerDeltaHook` / `EventBusPreToolHook` /
`EventBusPostToolHook` / `EventBusWebSearchHook` (`events/hooks.py`, wired into every `AgentLoop`
by `orchestrator/orchestrator.py::_build_loop`) publish the corresponding events. Answer sentences
are buffered incrementally (`guardrails.split_sentences_streaming`) so nothing unscanned is
forwarded; the whole-answer `check_output` pass (caveat append + final redaction) still runs in
`_finalize_output`, so the deltas are a preview, not a replacement for the authoritative scan.
`tool.start` exists because most configured models emit no thinking deltas, so a slow multi-tool
Step would otherwise show nothing live.

### Timeline / run-tree the frontend materializes
The client folds the stream into a tree the timeline renders as lanes:
```
turn
 └─ root (main agent)                     ← message/thinking/tool events on agent_id="root"
     ├─ route.decided → framework=subagent
     ├─ agent.spawned chip-flow   (lane A) ┐ parallel lanes, each with its own
     ├─ agent.spawned earnings    (lane B) │ thinking.delta / tool.* / progress,
     ├─ agent.spawned valuation   (lane C) ┘ interleaved but attributable via agent_id
     ├─ agent.done A/B/C
     └─ synthesis.start → message.delta (root) → turn.end
```
- **Parallel lanes** (framework `subagent`): lanes A/B/C emit concurrently; the client places
  each event in its lane by `agent_id` and animates them side-by-side (like Claude Code's
  parallel task view). **Team** (sequential) renders the same lanes but ordered.
- **Ordering:** `seq` is globally monotonic per turn, so a single reconnect replay
  (`Last-Event-ID`) rebuilds every lane deterministically — no per-lane cursors needed.
- **Thinking visualization:** each lane shows its own live progress — but as **natural
  language**, not engineering internals (see below).

### What the timeline shows
The timeline is built from `agent.spawned.activity` (a templated one-liner), `tool.start.summary`
and `tool.result.summary` (natural-language labels, never arguments or raw results) and the
collapsed-by-default `thinking.delta` stream. Tool names/arguments are detail, not the primary
line. All narration text is governed like any other output (non-trading, no raw payloads).

### `ask_user` and locked data
- **`ask_user`** is a normal, always-runnable tool: it validates/echoes the question + options as
  a governed `rich_card` within the Step that calls it, and the model is expected to make it its
  last action for the turn. There is no turn pause/resume (`AgentLoop.run_turn`'s Step loop has no
  suspension point mid-dispatch); the answer (an option tap or follow-up message) starts a new
  turn. It is not an access gate.
- **Locked data** is the masking outcome of `tools.base.dispatch_tool` for an unentitled `sponsor`
  call (`MaskedTeaser`): the `tool.result` frame carries `guardrail_reason: "masked_teaser"`, the
  turn completes around the masked section, and the frontend renders it blurred (馬賽克) with an
  upgrade CTA.

### Resumability
- Clients send `Last-Event-ID` on reconnect; the server replays events after that id from
  the turn's event log. Enables mobile/flaky-network resume without re-running the turn.
- A turn streams under the id of its persisted user turn (`/agent/query`, and `edit`'s new user
  turn); `regenerate` creates no user turn and streams under a fresh id whose turn row exists only
  once the answer is saved. `stream_events.turn_id` is therefore **not** a foreign key: each row
  carries the `thread_id` it belongs to (a FK with `ON DELETE CASCADE`, so the log goes with the
  conversation) and the streamed turn id as plain text. Every streamed turn, regenerated or not,
  has its events in the replay log.
- A reconnect resolves the turn to follow as: the one running on this worker, else the one another
  worker announced for the thread (`turn_control.find`, owner only), else the thread's latest saved
  turn. The announced turn is what lets a reconnect during a regenerated answer follow that answer
  rather than replay the previous one's events.
- The replay log is pruned by the scheduler (`scheduler/retention.py`, leader pass, at most hourly):
  rows older than 7 days are deleted in slices of 5,000. A reconnect happens within minutes of a
  drop, so older rows are never read, and without this the table grew with every chat.
- Non-SSE clients can read the persisted turns via `GET /threads/{id}`.

## 3. Turn lifecycle

```
accepted → running ──► (tool call → pre_tool guardrail + sponsor-entitlement gate)
   │                      ├─ public: dispatch
   │                      └─ sponsor: entitled → dispatch | unentitled → MaskedTeaser (masked, turn continues)
   ├─ cancel (client) ──► cancelled
   └─ done | error
```
The client sees a masked call as a `tool.result` frame with `guardrail_reason: "masked_teaser"`,
and the final `message` text/rich cards reflect the masked section once the turn completes.

- **Cancellation:** `POST /threads/{id}/turns/{turn_id}/cancel` finds the live turn in
  `api.turn_registry.TurnRegistry`, and `RunningTurn.request_cancel()` both flips the
  cooperative `SimpleCancelToken` (checked between Steps) *and* hard-cancels the driving
  `asyncio.Task` — so a driver that never itself polls the token is still interrupted, not just
  one that cooperates. In-flight tool calls for the current Step are read-only and safe to let
  finish. Emits `turn.end{status: "cancelled"}`.
- **Cancel/steer across workers:** the registry is per process. With `EVENT_BUS_BACKEND=redis` the
  worker running a turn also announces it (`turn:running:{turn_id}`, `turn:thread:{thread_id}`,
  refreshed while it listens) and subscribes to `turn-ctl:{turn_id}`
  (`events/turn_control.py`). A `cancel`/`steer` request that lands on another worker finds the
  turn there (owner and thread are checked, so another user's turn is a 404/409), publishes the
  command, and the owner applies it with `RunningTurn.apply`. On the default memory backend the
  control is a no-op and only the local registry is used.
- **Idempotency:** `POST /agent/query` accepts an `Idempotency-Key`, recorded on the persisted
  user turn (`turns.idempotency_key`) so a retried request doesn't spawn a duplicate turn.
  `conversation.turn_lifecycle.TurnLifecycle` (a separate, tested state machine with its own
  in-process idempotency de-dupe map) exists as a building block but is not the class the live
  `/agent/query`/`regenerate`/`edit` HTTP path actually drives — that path is
  `api.turn_driver.start_turn_stream`/`_drive_turn`, which implements the same
  accepted→running→(done|cancelled|error) shape directly against the `TurnRegistry` +
  `ConversationStore`, not by calling into `TurnLifecycle`.

## 4. Sponsor-data masking (the only access gate)

There is no approval handshake. When a turn calls a `sponsor` (paid FinMind) tool for an
unentitled user, the flow is fully automatic:

1. Agent proposes e.g. `get_chip_flows` (`data_class: sponsor`).
2. `tools.base.dispatch_tool` checks `is_entitled(tool.data_class, ctx.tier)`, where `ctx.tier`
   comes from the per-request `ToolContext` built by `accounts/entitlements` (via
   `api.dependencies.tool_context_for`, resolved fresh per call, never cached for the turn).
   Entitled → dispatch normally.
3. Unentitled → `dispatch_tool` returns a **masked teaser** (`MaskedTeaser{locked, required_tier,
   preview, tool_name}`) instead of running the tool. The turn keeps running; the outcome is
   reflected in that Step's `tool.result` SSE frame as `guardrail_reason: "masked_teaser"` (there
   is no separate `tool.locked` event).
4. The final answer is composed around the masked section; the frontend renders it blurred
   (馬賽克) with an "upgrade to Premium/Pro" CTA badge.

Both the attempt and its `data_class`/locked outcome are journaled (`agent/journal.py`). Identical
behavior over MCP (no special-casing) — `mcp.resources` dispatches through the same
`tools.base.dispatch_tool` gate.

## 5. Endpoints (added to `api/routers/`)

```
POST   /threads                            create thread
GET    /threads                            list threads (no sort/filter/q query params yet — see 13-chat-interaction-features.md)
GET    /threads/{id}                       thread + history
PATCH  /threads/{id}                       rename/star/archive/tag (If-Match version CAS)
DELETE /threads/{id}                       delete (soft-delete)
GET    /threads/search                     title + message-content search
GET    /threads/{id}/export                export as md|json
POST   /threads/{id}/fork                  fork from a turn (no auto branch-summary yet — see §1)
POST   /threads/{id}/switch-branch         switch active path to another message (no auto branch-summary yet — see §1)
POST   /agent/query                        start a turn (SSE)        # thread_id optional → auto-creates
GET    /threads/{id}/stream                resume/attach live stream (SSE, Last-Event-ID)
POST   /threads/{id}/steer                 queue a steering message into the running turn (no cancel)
POST   /threads/{id}/turns/{tid}/cancel    cancel a running turn
POST   /threads/{id}/turns/{tid}/regenerate  re-run the source user turn as a new sibling assistant turn (SSE)
POST   /threads/{id}/turns/{tid}/edit      edit a user turn + resend, creating a new branch (SSE)
POST   /threads/{id}/turns/{tid}/feedback  👍/👎 + optional comment (upserted per (turn_id, user_id))
```

## Governance obligations
- Read-only w.r.t. market data; the only writes are conversation/journal to the store.
- No credentials persisted; provider keys stay in env.
- Every tool dispatch (with `data_class` and locked/served outcome) is journaled.
