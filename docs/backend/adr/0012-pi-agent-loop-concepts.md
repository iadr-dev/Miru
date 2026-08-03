# ADR 0012 — Adopt Pi agent-loop concepts (steering, branch summary, deferred tools, output caps)

## Status
Accepted. Refines the agent runtime ([modules/05](../modules/05-agent-runtime.md)),
conversation ([modules/07](../modules/07-conversation-streaming.md)), and tools
([modules/03](../modules/03-tools.md)).

## Context
Studied the **Pi** coding agent's architecture (a Claude Code-style agent). Our design already
had the backbone (agent loop, Trace/Turn, compaction, memory, tiered tool registry,
progressive skill loading). Pi surfaced five refinements worth adopting; the rest (runtime
security over prompt security, param validation, Trace/Turn model) we already had.

## Decision
1. **Two-stage context build per turn:** make `transform_context` (compaction + memory +
   branch summaries at our internal AgentMessage layer) and `convert_to_llm` (→ provider
   payload/tool-call format, in the LLM adapter) explicit stages.
2. **Steering messages:** a user message sent *while a turn runs* is queued and injected at
   the next `turn_start` — redirects without cancelling. New `POST /threads/{id}/steer` +
   `steering.accepted` SSE event. Distinct from `/cancel`.
3. **Message tree + Branch Summary:** messages carry `parent_id` (tree); only the active path
   is sent to the LLM. On `switch-branch`, summarize the abandoned branch into a
   `BranchSummaryEntry` injected at the target so conclusions aren't lost.
4. **Compaction formula + split rule:** `contextTokens > contextWindow − reserveTokens`
   triggers compaction; **split only at Assistant-Message boundaries, never between a Tool
   Call and its Tool Result**; over-long single turns summarize history + first-half separately.
5. **Deferred/lazy tool loading:** split the ~35 tools into a small **immediate** core (full
   schemas always sent) and a **deferred** long tail (exposed as a name+description index,
   full schema loaded on demand) — mirrors progressive skill loading.
6. **Tool output size control:** cap tool output; on overflow, persist full result to a
   file/artifact and return a summary + key rows + a re-readable path, never the raw dump.

## Consequences
- Better chat UX (steer mid-run), lower token cost (deferred tools, output caps), and
  branch-aware memory — at the cost of more moving parts in the loop/store.
- Governance unchanged: steering/branch/compaction summaries are caveated, non-trading, and
  journaled; runtime remains the security boundary (not prompts).
- `messages.parent_id` + `branch_summary`/`compaction`/`steering` message types added to the
  schema; new steer/switch-branch endpoints.
