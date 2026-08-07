# Module: Orchestration (`backend/src/orchestrator/`)

Decides **how much machinery** a message needs — from a no-tool reply to a fleet of
parallel sub-agents — and which skills to run. The choice is **dynamic per query** (an LLM
intent classifier), not hardcoded — our hybrid classify-then-route model (`none | single |
subagent | team`), specified in this module.

## 1. Intent classifier

A cheap, fast LLM call (small model, ~200 tokens) that reads the user message + the list
of skill descriptions and returns a routing decision:

```python
class Routing(BaseModel):
    framework: Literal["none", "single", "subagent", "team"]
    skills: list[str]          # 0–4 skills, chosen from the registry
    reason: str                # short rationale (journaled)
```

- **Prompt** = `strategy_router` (message + skill `description`s). Same idea as skill
  auto-selection in [08-skills.md](08-skills.md), lifted to also pick the *framework*.
- **Fallback:** if the classifier call fails, keyword-match skills against their `triggers`
  ([08](08-skills.md)) and **default the framework to `single`** (the safe, cheap floor) — never
  hard-fail routing. (An unambiguous `triggers` hit may also pre-pin a skill as a fast-path.) For
  genuinely ambiguous intent, prefer `single` + an `ask_user` clarification over guessing `team`.
- The decision is emitted as an internal `route.decided` event and journaled.
- **Routing is evaluated:** classifier accuracy (framework+skill labels on a golden query set)
  is a first-class regression eval before shipping — misrouting is the highest-leverage error.

## 2. Frameworks

| Framework | When | Execution | Skills |
|-----------|------|-----------|--------|
| `none` | greeting / off-topic / capability question | reply from system prompt, **no tools** | [] |
| `single` | one-dimension lookup or action | 1 agent, ReAct loop | 1 |
| `subagent` | multi-angle analysis (independent dimensions) | **N skills → N parallel agents** → synthesis | 2–4 |
| `team` | sequential deep research (findings build on each other) | N agents with **shared context**, run in order → synthesis | 2–4 |

### `single` — ReAct loop
The standard agent loop from [05-agent-runtime.md](05-agent-runtime.md): think → tool →
observe → repeat, streaming over SSE. One skill's tool allowlist applies.

### `subagent` — parallel fan-out
```
route: {framework: subagent, skills: [chip-flow-review, earnings-quality, valuation-band]}
  ├─ spawn one sub-agent per skill (each = its skill's instructions + tool allowlist)
  ├─ run ALL concurrently (asyncio.gather); each is independent, no shared state
  ├─ each sub-agent is read-only → safe to fan out
  └─ synthesis agent combines results into one caveated, non-trading report
```
Sub-agents are read-only and independent; any `sponsor` data a sub-agent touches is subject
to the same entitlement masking, surfaced in the synthesized answer.

**Live-timeline streaming:** each spawn emits `agent.spawned{agent_id, parent_agent_id, role,
skill, activity}` and every event from that sub-agent carries its `agent_id` — so the frontend
renders one **parallel lane per sub-agent** with its own natural-language `activity` narration,
Claude Code-style. `route.decided` opens the tree; `synthesis.start` marks the merge. See the
correlation envelope + run-tree in [07-conversation-streaming.md](07-conversation-streaming.md).

### `team` — sequential collaboration
```
route: {framework: team, skills: [company-research, corporate-actions, derivatives-positioning]}
  ├─ teammate A runs → emits a structured `finding` (decision+cause+key_figures)
  ├─ teammate B runs WITH A's finding record (not raw transcript), ... (order matters)
  └─ team-lead synthesizes
```
Use when later analysis depends on earlier findings (news → data → risk). Hand-off is the
**structured finding**, not full output (see §3a); a per-hop sanity check limits cascade error.

## 3. Synthesis

A dedicated synthesis pass (best-quality model) merges sub-agent/team outputs into one
answer. It runs `guardrails.check_output` (non-trading, caveats) like any final output, and
records which skills+versions contributed (provenance for the conversation-package handoff).

- **Partial results:** synthesis proceeds with **≥1 successful lane** and must **declare which
  dimensions are missing and why** ("chip-flow lane unavailable — provider timeout"). The turn
  fails only if **zero** lanes return.
- **Conflicting lanes:** synthesis never silently picks a winner — it **surfaces both
  conclusions with provenance and caveats the divergence** (aligns with non-trading rule).
- **Synthesis fallback:** if the synthesis call itself fails (fallback chain exhausted), emit a
  **structured concatenation of the per-lane findings** (each already guardrail-checked) with a
  "synthesis degraded" banner — never discard successful lanes. Still passes `check_output`.
- **Per-lane output guardrail:** each sub-agent's output passes `pre_output` (non-trading scan)
  **before** it enters synthesis, so trading language cannot be laundered into synthesis input
  and paraphrased past the final check — enforcement at two layers.

## 3a. Inter-agent context contract (what each agent receives)

Explicit, to avoid both context-budget blowup and silent starvation:
- **Parallel sub-agents (`subagent`)** receive a **scoped slice** — the user query + resolved
  entities/constraints + the relevant memory block + their skill body — **NOT the full
  transcript**. They run on the **post-compaction** context; they never re-inflate history.
  (4 lanes × full history would multiply tokens 4× and can exceed the window.)
- **Teammates (`team`)** hand off **structured `finding` records** (per [09-memory.md](09-memory.md)
  summary-quality: decision + cause + key_figures), **not raw prose** — bounding growth across
  the sequence. A lightweight schema/sanity check runs on each shared-context write to stop a
  wrong early finding from cascading (`team` is reserved for genuine dependency and capped at 4).
- **Agent output contract:** every sub-agent returns a **structured result with a
  completeness/confidence signal** (not just prose), so synthesis knows an empty/low-confidence
  lane from a good one.

## 3b. Failure semantics & ceilings

- **Fan-out isolation:** lanes run under `asyncio.gather(..., return_exceptions=True)` (or
  per-lane try/except) — one lane's error/timeout/quota-trip **never** kills siblings; it
  becomes a lane-scoped `error`/degradation SSE event and a missing dimension in synthesis.
- **Agent-level fallback:** a lane that returns schema-invalid/empty → one corrective retry →
  else drop-with-flag. Sub-agents are read-only + independent, so retry is idempotent.
- **Per-turn ceilings (hard, enforced in `pre_turn`/`pre_llm` hooks):** max sub-agents = **4**;
  max ReAct iterations per agent ≈ **8–10**; max total tool calls per turn; **per-turn cost/token
  ceiling** → abort to a structured degraded answer, not a crash. Bounds runaway loops.
- **FinMind pre-flight reservation:** before spawning a fan-out, the quota manager reserves the
  lanes' Sponsor budget; if unavailable, lanes degrade to cached/`unavailable-tier` **before**
  spawning (a distinct caveated lane-degradation, not a generic error). See [16](16-infrastructure.md) §4.
- **Steering during fan-out:** buffered and applied at synthesis / next root turn — it **never
  mutates an in-flight lane's context** (lanes are short-lived, read-only; no race).

## 4. Model routing (uses the provider-agnostic LLM adapter)

Cost/latency-tiered, per role — configured, not hardcoded:

| Role | Preferred tier | Why |
|------|----------------|-----|
| Intent classifier | small/cheap (e.g. Haiku/Flash-class) | ~200 tokens, high volume |
| Single agent | strong tool-use model (e.g. Sonnet) | reliability of tool calls |
| Parallel sub-agents | cheap/fast | many at once, bounded scope |
| Synthesis | best writing model | final quality |
| **Batch summarization** (AI summary/SWOT [20](20-analytics-scoring.md) §3; compaction/branch-summary [05](05-agent-runtime.md)) | **cheap-tier** | summarization over structured facts, not reasoning — high volume, off the request path |

This is the "cheap-tier model" that [20](20-analytics-scoring.md) §3 and [05](05-agent-runtime.md)
cite — the same tier as the classifier/sub-agents, applied to non-reasoning summarization work.

Provider selection + fallback chains live in the LLM adapter
([04-llm-adapter.md](04-llm-adapter.md)); the orchestrator only names the *role*, the
adapter maps role → provider/model from config.

## 5. Governance obligations
- Every sub-agent/teammate inherits its skill's **tool allowlist** (enforced by the registry;
  a call outside the allowlist is refused); guardrail hooks run on each independently.
- **Non-trading enforced at two layers:** `pre_output` runs on **each sub-agent output before
  it enters synthesis** AND on the **final synthesized answer** — a sub-agent cannot launder
  trading language into synthesis to be paraphrased past the final check.
- `sponsor` data accessed by any sub-agent is entitlement-masked identically
  ([07](07-conversation-streaming.md)); there is no approval step to coordinate.
- The routing decision, each agent's tools (with `agent_id`), and synthesis provenance are
  journaled — a fan-out is reconstructable per lane.
- Bounded by config: max **4** sub-agents, per-agent ReAct-iteration cap, per-turn tool-call
  and **cost/token ceiling** (hard abort → degraded answer), plus the FinMind Sponsor pool's
  static **6000 req/hr** ceiling (shared across all Premium/Pro users; Free never draws on it)
  with pre-flight reservation before fan-out. See §3b + [16](16-infrastructure.md).
