# Module: Conversation & streaming (`backend/src/conversation/`, `backend/src/api/`)

The chat backbone: persistent threads, a typed streaming event protocol, and turn lifecycle
(cancel/resume). This is what turns the agent loop into a real interactive chat backend.
There is **no approval handshake** — tools run freely; paid (`sponsor`) data simply streams
back as a masked teaser for unentitled users.

## 1. Conversation model

Messages form a **tree** (Pi-style): each message has `id` + `parentId`, so a thread can
branch. The whole tree is persisted (JSONL/table); only the **active path** is sent to the LLM.

```
Thread            # a conversation (a message tree)
 ├─ id, title, created_at, updated_at, metadata (watchlist, provider/model)
 └─ Turn[]        # one user request → one agent response cycle (a Turn on the active path)
      ├─ id, role (user|assistant), status (running|done|cancelled|error), parent_turn_id
      ├─ Message[]   # ordered blocks: text, thinking, tool_use, tool_result, caveat, locked
      ├─ journal_ref # link to the auditable tool journal (conversation/session.py)
      └─ usage        # tokens, tool calls, cost
```

### Persistence
- Store: `ConversationStore` protocol (`conversation/store.py`); SQLite dev default,
  Postgres in production. Full schema: [../database-schema.md](../database-schema.md).
- Consistent with governance: **no credentials persisted** — only conversation content, tool
  journal, and derived context. The canonical package remains immutable and separate.
- Operations: `create_thread`, `list_threads`, `get_thread(history)`, `append_turn`,
  `fork_thread(from_turn)`, `switch_branch(to_message)`, `delete_thread`.

### Branch switching + Branch Summary (Pi `/tree`) — the token-efficient default
**Prefer `switch_branch` over `fork_thread` for exploring alternatives.** Because history is
one tree, switching branches does **not** copy or re-inflate context — only the active path
is ever sent to the LLM. When the user switches from one branch to another (e.g. C→E in
`A─B─C \ D─E`) we:
1. find the two branches' common ancestor; 2. collect the messages on the branch being left;
3. summarize them (cheap model); 4. inject a **BranchSummaryEntry** at the target position.
So switching keeps the *conclusions* of the branch left behind ("tried approach X → failed
because …") at ~1% of the tokens — **less context, memory preserved**. Summaries are caveated
and journaled.

### `fork_thread` vs `switch_branch` — when to use which
| | `switch_branch` (default) | `fork_thread` (rare) |
|--|---------------------------|----------------------|
| Model | in-place tree, active-path swap | new thread |
| Context cost | **low** — only active path + branch summaries | avoid full re-inflation |
| Memory of other branch | kept as BranchSummaryEntry | source path summarized in, not copied verbatim |
| Use when | "try another approach", edit-and-resend, compare | genuinely separate conversation you want to keep apart |

Fork is implemented as a **reference to the shared tree + a summary of the source path** — it
must **not** deep-copy full history into the new thread (that was the naive, token-heavy
pattern). Same principle as compaction: carry the *summary*, not the raw transcript.

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
| `tool.result` | `{call_id, summary, source_health}` | tool returned (raw payload withheld) |
| `tool.locked` | `{call_id, name, required_tier, preview}` | **`sponsor` data, unentitled** — masked teaser (馬賽克 + upgrade CTA) |
| `tool.blocked` | `{call_id, reason}` | guardrail refused (trading intent / full-market scan) |
| `caveat` | `{kind, text}` | currentness / non-trading caveat surfaced |
| `synthesis.start` | `{from_agents:[...]}` | main agent begins merging sub-agent results |
| `rich_card` | `{card_type, data}` | structured UI card (stock card, comparison table, chart, gauge) |
| `ask_user` | `{prompt, options?, kind}` | agent needs clarification (ambiguous symbol, missing param) |
| `follow_up` | `{suggestions:[...]}` | suggested next questions/actions (chips) |
| `pet.reaction` | `{pet_id, cue}` | **cosmetic** companion avatar cue ([21](21-pet-gamification.md)) — separate UI region, ignorable |
| `pet.leveled` | `{pet_id, level}` | equipped pet leveled up mid-stream (cosmetic) |
| `pet.pull_earned` | `{available_pulls}` | a milestone granted a gacha pull (cosmetic) |
| `usage` | `{agent_id, input_tokens, output_tokens, tool_calls, cost}` | running accounting (per agent) |
| `turn.end` | `{turn_id, status}` | completed |
| `error` | `{agent_id?, code, message, retryable}` | terminal or recoverable error (per lane if scoped) |

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

Two tracks, deliberately separated:
1. **Narration track (default view):** `activity` + `agent.spawned.activity`/`agent.progress.activity`
   — the readable story of the run. This is what the timeline renders as lane lines. The
   backend generates each `activity` label when it proposes a tool / spawns an agent / starts a
   phase (a cheap templated or model-written one-liner derived from intent + args), so the
   frontend never has to reverse-engineer it from `tool.name`.
2. **Detail track (opt-in "show details"):** `tool.proposed.name/args`, `tool.result.summary`,
   and **`thinking.delta`** (raw reasoning) are still streamed for power users / debugging, but
   are **collapsed by default**. `thinking.delta` is never the primary display — some providers
   don't expose it, and it's not user-facing prose.

So the frontend shows a clean "thinking process" as a sequence of NL activity lines per lane;
tapping a node reveals the underlying tool/args/result. All `activity` text is governed
(caveated, non-trading, no raw payloads) like any other output.

**Web/mobile note.** `rich_card`, `ask_user`, and `follow_up` make the chat feel native on a
GUI (structured cards, inline clarification, tappable chips). `rich_card` payloads are
governed — caveated, non-trading, no raw source payloads.

### Inline user input & locked data
- **`ask_user`** — *informational* clarification (which 台積電? what window?). Answered by a
  normal follow-up message or an option tap. Not an access gate.
- **`tool.locked`** — *entitlement* signal: the agent tried a `sponsor` (paid) tool for an
  unentitled user. The stream carries `{required_tier, preview}` so the frontend renders the
  section blurred (馬賽克) with an "upgrade to Premium/Pro" CTA. No user action is required for
  the turn to complete — the answer proceeds around the masked section.

### Resumability
- Clients send `Last-Event-ID` on reconnect; the server replays events after that id from
  the turn's event log. Enables mobile/flaky-network resume without re-running the turn.
- Non-SSE clients can poll `GET /threads/{id}/turns/{turn_id}` for the materialized state.

## 3. Turn lifecycle

```
accepted → running ──► (tool.proposed → guardrail)
   │                      ├─ public: dispatch
   │                      └─ sponsor: entitled → dispatch | unentitled → tool.locked (teaser)
   ├─ cancel (client) ──► cancelled
   └─ done | error
```

- **Cancellation:** `POST /threads/{id}/turns/{turn_id}/cancel` sets a cancel flag the
  loop checks between steps; in-flight tool calls are allowed to finish (read-only, safe)
  but no new step starts. Emits `turn.end{status:cancelled}`.
- **Idempotency:** `POST /agent/query` accepts an `Idempotency-Key` so a retried request
  doesn't spawn a duplicate turn.

## 4. Sponsor-data masking (the only access gate)

There is no approval handshake. When a turn calls a `sponsor` (paid FinMind) tool for an
unentitled user, the flow is fully automatic:

1. Agent proposes e.g. `get_chip_flows` (`data_class: sponsor`).
2. The `pre_tool` hook checks `accounts/entitlements`. Entitled → dispatch normally.
3. Unentitled → the tool returns a **masked teaser** (`{locked, required_tier, preview}`) and
   the server emits `tool.locked{required_tier, preview}`. The turn keeps running.
4. The final answer is composed around the masked section; the frontend renders it blurred
   (馬賽克) with an "upgrade to Premium/Pro" CTA badge.

Both the attempt and its `data_class`/locked outcome are journaled. Identical behavior over
MCP (no special-casing).

## 5. Endpoints (added to `api/routers/`)

```
POST   /threads                          create thread
GET    /threads                          list threads
GET    /threads/{id}                     thread + history
DELETE /threads/{id}                     delete
POST   /threads/{id}/fork                fork from a turn
POST   /threads/{id}/switch-branch       switch active path to another message (→ Branch Summary)
POST   /agent/query                      start a turn (SSE)        # thread_id optional → auto-creates
GET    /threads/{id}/stream              resume/attach live stream (SSE, Last-Event-ID)
POST   /threads/{id}/steer               queue a steering message into the running turn (no cancel)
POST   /threads/{id}/turns/{tid}/cancel  cancel a running turn
```

## Governance obligations
- Read-only w.r.t. market data; the only writes are conversation/journal to the store.
- No credentials persisted; provider keys stay in env.
- Every tool dispatch (with `data_class` and locked/served outcome) is journaled.
