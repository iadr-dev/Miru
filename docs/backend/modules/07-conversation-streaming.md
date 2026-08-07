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
      ├─ Message[]   # ordered blocks: text, thinking, tool_use, tool_result, caveat, rich_card,
      │              # steering, branch_summary, compaction (conversation/models.py MessageBlockType)
      └─ usage        # tokens, tool calls, cost
```

### Persistence
- Store: `ConversationStore` protocol (`conversation/store.py`); SQLAlchemy async, SQLite dev
  default, Postgres in production. Full schema: [../database-schema.md](../database-schema.md).
- Consistent with governance: **no credentials persisted** — only conversation content, tool
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
| Event | Payload (+ envelope) | Meaning |
|-------|---------|---------|
| `turn.start` | `{turn_id, thread_id}` | turn accepted |
| `route.decided` | `{framework, skills, reason}` | orchestrator routing (see [11](11-orchestration.md)) |
| `agent.spawned` | `{agent_id, parent_agent_id, role, skill, activity}` | a sub-agent lane opened; `activity` = NL label |
| `agent.progress` | `{agent_id, status, phase, activity}` | lifecycle `queued→running→done\|error` + NL line |
| `agent.done` | `{agent_id, status, summary}` | sub-agent lane closed |
| `activity` | `{node_id, text}` | **natural-language narration** of what's happening now (the primary timeline line) |
| `steering.accepted` | `{text}` | mid-run user message queued, injected next turn (no cancel) |
| `message.delta` | `{node_id, text}` | streamed assistant answer token(s) |
| `thinking.delta` | `{node_id, text}` | raw reasoning stream — **collapsed by default** (see below) |
| `tool.proposed` | `{call_id, name, args, data_class, activity}` | tool call + its NL label (`activity` shown, `name/args` are detail) |
| `tool.started` | `{call_id}` | dispatch began (post-guardrail) |
| `tool.result` | `{step_index, tool_name, argument_keys, summary, guardrail_allowed, guardrail_reason}` | tool returned (raw payload withheld) |
| `tool.locked` | `{call_id, name, required_tier, preview}` | **`sponsor` data, unentitled** — masked teaser (馬賽克 + upgrade CTA) |
| `tool.blocked` | `{call_id, reason}` | guardrail refused (trading intent / full-market scan) |
| `caveat` | `{kind, text}` | currentness / non-trading caveat surfaced |
| `message` | `{text, framework}` | **real** — the one final-answer frame the driver publishes just before `turn.end`; not chunked/streamed token-by-token (see `message.delta` below) |
| `synthesis.start` | `{from_agents:[...]}` | main agent begins merging sub-agent results |
| `rich_card` | `{card_type, data}` | structured UI card (stock card, comparison table, chart, gauge) — **real**, see status note below |
| `ask_user` | `{prompt, options?, kind}` | agent needs clarification (ambiguous symbol, missing param) |
| `follow_up` | `{suggestions:[...]}` | suggested next questions/actions (chips) |
| `pet.reaction` | `{pet_id, cue}` | **cosmetic** companion avatar cue ([21](21-pet-gamification.md)) — separate UI region, ignorable |
| `pet.leveled` | `{pet_id, level}` | equipped pet leveled up mid-stream (cosmetic) |
| `pet.pull_earned` | `{available_pulls}` | a milestone granted a gacha pull (cosmetic) |
| `usage` | `{agent_id, input_tokens, output_tokens, tool_calls, cost}` | running accounting (per agent) |
| `turn.end` | `{turn_id, status}` | completed |
| `error` | `{agent_id?, code, message, retryable}` | terminal or recoverable error (per lane if scoped) |

**Implementation status.** Real today: `turn.start`, `route.decided` (published by
`orchestrator.classifier.IntentClassifier.classify`), `agent.spawned`, `agent.progress`
(only ever `{status: "degraded", phase: "quota"}` today — no other progress phase is published),
`agent.done`, `steering.accepted` (recorded on `RunningTurn.steering` and drained by
`AgentLoop._inject_steering` at the next Step boundary for the `single`-framework interactive
root lane — genuinely redirects a running turn, proven end-to-end by
`tests/api/test_steering_wiring.py`; the earlier claim that this event was accepted-but-inert is
now stale), `synthesis.start`, `usage` (published once per turn by
`orchestrator.orchestrator.Orchestrator._finalize`, not truly "running" per-step accounting),
`turn.end`, `message` (the one final-answer frame, not chunked), and `tool.result` (published by
`events/hooks.py`'s `EventBusPostToolHook` on every tool dispatch — wired into every `AgentLoop`
build across the `single`/`subagent`/`team` frameworks via `orchestrator/orchestrator.py`'s
`_build_loop`, and streamed live over SSE via the bus subscription in `api/turn_driver.py`), and
`rich_card` (built end-to-end: emitted on the `single`, `subagent`, and `team` frameworks alike,
governed via a structured non-trading scan on the payload, and never built from a masked/
unentitled tool result — see [03-tools.md](03-tools.md) for which tools opt into a card via a
`card_type` class attribute).

**Not real — no event of this kind is ever published anywhere in the codebase** (verified by
grepping every `event_bus.publish(...)` call site): `caveat` (the non-trading caveat text is
present *inside* the final `message`/persisted `TextBlock`'s prose, appended by
`agent.guardrails.check_output`, but there is no dedicated `caveat` SSE event carrying it
separately — the table row above documents the intended future shape, not current behavior),
`activity`, `message.delta`, `thinking.delta`, `tool.proposed`, `tool.started`, `tool.locked`
(the masking outcome itself is real — `tools.base.dispatch_tool` returns a `MaskedTeaser` and
the resulting `tool.result` frame carries `guardrail_reason: "masked_teaser"` — but there is no
separate `tool.locked` event kind), `tool.blocked`, `ask_user`, `follow_up`,
`pet.reaction`/`pet.leveled`/`pet.pull_earned`, `error` (a failed/cancelled turn instead
publishes `turn.end` with `data.status` of `"error"`/`"cancelled"` and an inline `message`
field — there is no standalone `error`-kind event). The agent loop currently returns one
completed `TurnResult` per turn rather than streaming token-level events, so the API layer
publishes only the coarser events above (plus the real `tool.result`) and a single final
`message` block. Building the missing kinds is a larger, separate piece of work than
`rich_card`/`tool.result` was, since it requires token-level streaming and the narration track
described below, not just a new event type.

### Timeline / run-tree the frontend materializes
The client folds the stream into a tree the timeline renders as lanes:
```
turn
 └─ root (main agent)                     ← message/thinking/tool events on agent_id="root"
     ├─ route.decided → framework=subagent
     ├─ agent.spawned chip-flow   (lane A) ┐ parallel lanes, each with its own
     ├─ agent.spawned earnings    (lane B) �│ thinking.delta / tool.* / progress,
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

### Natural-language narration (what the timeline actually shows)
The timeline's primary line for every node is the **`activity`** field — a short, human,
present-tense sentence describing *what the agent is doing*, e.g.:
- ✅ "Checking 台積電 institutional flows over the last 20 days"
- ✅ "Comparing 0050 vs 006208 holdings and fees"
- ✅ "Reading the latest 重大訊息 for 欣興"
- ❌ NOT `tool.proposed{name:"get_chip_flows", args:{stock_id:"2330", window:20}}`
- ❌ NOT raw chain-of-thought / provider tokens

Two tracks, deliberately separated (intended design — see status note):
1. **Narration track (default view):** a standalone `activity` event + `agent.spawned.activity`/
   `agent.progress.activity` — the readable story of the run. This is what the timeline would
   render as lane lines. **Implementation status:** `agent.spawned.activity` is real today (a
   templated one-liner, `f"Running {skill_name}"`, set in `orchestrator.orchestrator
   ._run_one_lane`); `agent.progress` is real but never carries an `activity` field (its only
   real payload is `{status: "degraded", phase: "quota"}`); the standalone `activity` event kind
   does not exist.
2. **Detail track (opt-in "show details"):** `tool.proposed.name/args` and **`thinking.delta`**
   (raw reasoning) are not implemented; `tool.result.summary` is real and already streamed.
   `thinking.delta` is never meant to be the primary display — some providers don't expose it,
   and it's not user-facing prose.

So the frontend would show a clean "thinking process" as a sequence of NL activity lines per
lane; tapping a node would reveal the underlying tool/args/result. All `activity` text is meant
to be governed (caveated, non-trading, no raw payloads) like any other output, once built.

**Web/mobile note.** `rich_card` is real; `ask_user` and `follow_up` are not built. Once all
three exist they would make the chat feel native on a GUI (structured cards, inline
clarification, tappable chips). `rich_card` payloads are governed today — caveated, non-trading,
no raw source payloads.

### Inline user input & locked data
Neither of these is a real SSE event kind yet (see the implementation-status note above) — this
section describes the intended UX once the token-level streaming work lands.
- **`ask_user`** — *informational* clarification (which 台積電? what window?). Answered by a
  normal follow-up message or an option tap. Not an access gate.
- **`tool.locked`** — *entitlement* signal: the agent tried a `sponsor` (paid) tool for an
  unentitled user. The stream would carry `{required_tier, preview}` so the frontend renders the
  section blurred (馬賽克) with an "upgrade to Premium/Pro" CTA. What's real today: the masking
  decision itself (`tools.base.dispatch_tool` returns a `MaskedTeaser` for an unentitled
  `sponsor` call, and the resulting `tool.result` frame carries
  `guardrail_reason: "masked_teaser"`) — just not as its own `tool.locked` event. No user action
  is required for the turn to complete — the answer proceeds around the masked section.

### Resumability
- Clients send `Last-Event-ID` on reconnect; the server replays events after that id from
  the turn's event log. Enables mobile/flaky-network resume without re-running the turn.
- Non-SSE clients can poll `GET /threads/{id}/turns/{turn_id}` for the materialized state.

## 3. Turn lifecycle

```
accepted → running ──► (tool call → pre_tool guardrail + sponsor-entitlement gate)
   │                      ├─ public: dispatch
   │                      └─ sponsor: entitled → dispatch | unentitled → MaskedTeaser (masked, turn continues)
   ├─ cancel (client) ──► cancelled
   └─ done | error
```
The per-call decision above (`tool.proposed`/`tool.locked` as separate SSE events in the table
in §2) is not streamed as its own event yet — the client sees the outcome as a `tool.result`
frame with `guardrail_reason: "masked_teaser"` when masked, and the final `message` text/rich
cards reflect the masked section once the turn completes; see the §2 implementation-status note.

- **Cancellation:** `POST /threads/{id}/turns/{turn_id}/cancel` finds the live turn in
  `api.turn_registry.TurnRegistry`, and `RunningTurn.request_cancel()` both flips the
  cooperative `SimpleCancelToken` (checked between Steps) *and* hard-cancels the driving
  `asyncio.Task` — so a driver that never itself polls the token is still interrupted, not just
  one that cooperates. In-flight tool calls for the current Step are read-only and safe to let
  finish. Emits `turn.end{status: "cancelled"}`.
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
   is no separate `tool.locked` event — see §2's implementation-status note).
4. The final answer is composed around the masked section; the frontend is meant to render it
   blurred (馬賽克) with an "upgrade to Premium/Pro" CTA badge once the frontend/event work lands.

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
