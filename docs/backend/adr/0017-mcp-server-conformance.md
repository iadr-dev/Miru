# ADR 0017 — MCP server: transport, annotations, search/execute, Directory conformance

## Status
Accepted. Refines [modules/06](../modules/06-interfaces.md) (MCP server) and
[modules/03](../modules/03-tools.md).

## Context
A review against MCP best practices + the **Anthropic Directory review criteria** (pass/fail)
found the MCP design sound but missing conformance details: transport unstated, no tool
annotations (a hard Directory requirement), ~50 tools exceeding the per-turn schema budget over
MCP, and unstated structured-output/error/large-result handling. Our internal deferred-tool
loading solves context economics for the agent loop but not for external MCP clients, which see
the full tool list.

## Decision
1. **Transport = remote streamable-HTTP** (hosted, multi-user; external agents add a URL). Not
   local stdio.
2. **Tool annotations on every MCP tool:** `readOnlyHint:true`, `destructiveHint:false`,
   `idempotentHint:true`, `openWorldHint:true`, plus `title`. All tools are read-only → hosts
   may auto-approve (a UX win, and satisfies the annotation requirement).
3. **Immediate + search/execute exposure:** register the small core directly; expose the
   deferred long tail via `search_tools(intent)` / `execute_tool(id, args)` so the MCP client's
   context stays lean (mirrors internal deferred loading).
4. **Structured output:** Pydantic `output_model` as `structuredContent` + JSON text fallback.
5. **Large results:** obey tool output caps — truncate with "showing N of M" or `resource_link`;
   never dump megabytes.
6. **Errors:** MCP `isError` results with recovery hints, never raised exceptions.
7. **Descriptions declarative-only** (no "always call X" behavior instructions — Directory
   prompt-injection rule); the shared `Tool.description` is written to that bar.

## Consequences
- The MCP surface is submittable to the Anthropic Directory (annotations, name ≤64, read-only,
  no injection-y descriptions, structured output).
- `search_tools`/`execute_tool` added as two `public` MCP tools; the immediate/deferred split is
  the single source shared with the internal loop's deferred loading.
- No governance change — read-only, `sponsor` masking, non-trading, principal/entitlement model
  ([adr/0010](0010-remove-safety-tiers.md)) all hold.
