# ADR 0013 — Live-timeline streaming with parallel sub-agent lanes & NL narration

## Status
Accepted. Extends the SSE protocol ([modules/07](../modules/07-conversation-streaming.md)) and
orchestration ([modules/11](../modules/11-orchestration.md)).

## Context
The frontend (web/mobile) renders a **live timeline** of the agent's work — Claude Code-style
— including **parallel sub-agent lanes** for the `subagent`/`team` frameworks. The original SSE
events were flat: `message.delta`/`thinking.delta`/`tool.*` had no agent attribution, so the
client couldn't tell which lane a token belonged to. Separately, the timeline must show
**human-readable narration**, not engineering internals (raw tool-call JSON or chain-of-thought).

## Decision
1. **Correlation envelope on every event:** `{seq, ts, turn_id, agent_id, parent_agent_id,
   node_id}`. `agent_id="root"` for the main loop; sub-agents get their own ids. The client
   folds the stream into a **run tree** and renders one lane per `agent_id`.
2. **Sub-agent lifecycle events:** `agent.spawned` / `agent.progress` / `agent.done` +
   `synthesis.start`, so lanes open/close and the merge step is explicit.
3. **Natural-language narration is the primary display:** a new **`activity`** event (and
   `activity` fields on spawn/progress/tool.proposed) carries a short present-tense sentence
   ("Checking 台積電 institutional flows…"). The backend generates it at intent time. The
   timeline renders `activity`; raw `thinking.delta` and tool `name/args` are **detail-track,
   collapsed by default**.
4. **Deterministic replay:** `event_seq` is globally monotonic per turn and persisted in
   `stream_events` (with the envelope), so one `Last-Event-ID` replay rebuilds all lanes.
5. **Per-agent usage:** `usage` events carry `agent_id` for per-lane cost/token display.

## Consequences
- Frontend can render parallel lanes + per-lane live narration with no reverse-engineering.
- Backend must emit `activity` labels and thread the envelope through the event bus (the bus
  already exists; events gain envelope fields).
- `thinking.delta` stays optional (not all providers expose it) and never the primary line.
- Governance holds: `activity` is caveated, non-trading, no raw payloads — same as any output.
- Slightly larger event volume (envelope per frame); negligible vs. token/latency cost.
