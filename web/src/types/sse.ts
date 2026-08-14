/**
 * The streaming SSE envelope + the per-kind data payloads the chat turn consumes.
 *
 * Mirrors `backend/src/events/bus.py` (`Event`) and `backend/src/api/sse.py`
 * (`format_sse_event`): the wire frame is `id: <seq>\nevent: <kind>\ndata: <json>\n\n`, where the
 * JSON body is the correlation envelope `{seq, ts, turn_id, agent_id, parent_agent_id, node_id,
 * data}` — note the `kind` lives on the SSE `event:` line, NOT inside the JSON, so the parser must
 * pair the two back together. Parse-don't-trust: every frame is validated at the seam.
 *
 * The set of `kind`s here is the AUDITED-REAL vocabulary the backend actually publishes today
 * (see the orchestrator/turn-driver publish sites): `turn.start`, `route.decided`, `agent.spawned`,
 * `agent.progress`, `agent.done`, `tool.result`, `rich_card`, `synthesis.start`, `usage`,
 * `steering.accepted`, `message`, `turn.end`, plus `thinking.delta` — genuine incremental
 * model-reasoning chunks (Claude's `thinking_delta` tokens), registered the same way as the other
 * per-agent kinds above — and `web_search.result` — one completed native-web-search call's
 * source list (`events/hooks.py::EventBusWebSearchHook`), a whole unit like `tool.result`, never a
 * delta. There is still NO token streaming for the final answer itself (`message.delta` does not
 * exist) and no `activity`/`tool.proposed`/`tool.locked`/`caveat`/`follow_up` event — the final
 * answer arrives in one `message` frame just before `turn.end`, and errors ride on `turn.end`
 * (`{status: "error"}`), never a separate `error` event.
 */
import { z } from "zod";

/** The correlation envelope, projected verbatim from `Event` minus its `kind` (which is the SSE
 * `event:` field). `data` is an open object narrowed per-kind by the schemas below. */
export const sseEnvelopeSchema = z.object({
  seq: z.number().int().nonnegative(),
  ts: z.string(),
  turn_id: z.string().nullable().optional(),
  agent_id: z.string().nullable().optional(),
  parent_agent_id: z.string().nullable().optional(),
  node_id: z.string().nullable().optional(),
  data: z.record(z.string(), z.unknown()).default({}),
});
export type SseEnvelope = z.infer<typeof sseEnvelopeSchema>;

/** The real, currently-emitted event kinds. A discriminated `switch` over these in the reducer gets
 * an exhaustive-check arm so a newly-emitted kind becomes a compile error, not a silent miss. */
export const SSE_EVENT_KINDS = [
  "turn.start",
  "route.decided",
  "agent.spawned",
  "agent.progress",
  "agent.done",
  "tool.result",
  "thinking.delta",
  "web_search.result",
  "rich_card",
  "synthesis.start",
  "usage",
  "steering.accepted",
  "message",
  "turn.end",
] as const;
export type SseEventKind = (typeof SSE_EVENT_KINDS)[number];

export function isSseEventKind(value: string): value is SseEventKind {
  return (SSE_EVENT_KINDS as readonly string[]).includes(value);
}

// ---- per-kind data payloads (only the fields we render) ------------------------------------

/** `turn.start` — carries the resolved thread id, the one place a brand-new (auto-created) thread's
 * id surfaces to the client (mirrors `turn_driver._drive_turn`). */
export const turnStartDataSchema = z.object({ thread_id: z.string() });
export type TurnStartData = z.infer<typeof turnStartDataSchema>;

/** `route.decided` — the classifier's framework + chosen skills (`orchestrator/classifier.py`). */
export const routeDecidedDataSchema = z.object({
  framework: z.string(),
  skills: z.array(z.string()).default([]),
  reason: z.string().optional(),
});
export type RouteDecidedData = z.infer<typeof routeDecidedDataSchema>;

/** `agent.spawned` — one sub-agent lane started (`orchestrator.py` fan-out). */
export const agentSpawnedDataSchema = z.object({
  agent_id: z.string(),
  parent_agent_id: z.string().nullable().optional(),
  role: z.string().optional(),
  skill: z.string().optional(),
  activity: z.string().optional(),
});
export type AgentSpawnedData = z.infer<typeof agentSpawnedDataSchema>;

/** `agent.progress` — only ever `{status:"degraded", phase:"quota"}` today. */
export const agentProgressDataSchema = z.object({
  agent_id: z.string(),
  status: z.string(),
  phase: z.string().optional(),
});
export type AgentProgressData = z.infer<typeof agentProgressDataSchema>;

/** `agent.done` — a lane finished with a terminal lane status. */
export const agentDoneDataSchema = z.object({
  agent_id: z.string(),
  status: z.string(),
});
export type AgentDoneData = z.infer<typeof agentDoneDataSchema>;

/** `tool.result` — the redacted, safe-to-show summary (`events/hooks.py::EventBusPostToolHook`).
 * Never a raw payload; `summary` is the same string the journal uses. */
export const toolResultDataSchema = z.object({
  step_index: z.number().int().optional(),
  tool_name: z.string(),
  argument_keys: z.array(z.string()).default([]),
  summary: z.string().nullable().optional(),
  guardrail_allowed: z.boolean().optional(),
  guardrail_reason: z.string().nullable().optional(),
});
export type ToolResultData = z.infer<typeof toolResultDataSchema>;

/** `thinking.delta` — one incremental chunk of genuine model reasoning (Claude's `thinking_delta`
 * tokens), scoped to the agent/lane that produced it. Accumulate `text` client-side per `agent_id`,
 * same idea as any token stream — this is the first real per-token accumulation in this codebase
 * (everything else above is a one-shot lifecycle frame, never a delta). */
export const thinkingDeltaDataSchema = z.object({
  agent_id: z.string(),
  text: z.string(),
});
export type ThinkingDeltaData = z.infer<typeof thinkingDeltaDataSchema>;

/** `web_search.result` — one completed native-web-search call's source list
 * (`events/hooks.py::EventBusWebSearchHook`). Unlike `thinking.delta`, this is a whole unit, not
 * a delta to accumulate — a turn can emit several of these (one per search), each appended
 * as-is. */
export const webSearchSourceSchema = z.object({
  url: z.string(),
  title: z.string(),
});
export type WebSearchSource = z.infer<typeof webSearchSourceSchema>;

export const webSearchResultDataSchema = z.object({
  agent_id: z.string(),
  sources: z.array(webSearchSourceSchema).default([]),
});
export type WebSearchResultData = z.infer<typeof webSearchResultDataSchema>;

/** `synthesis.start` — the root lane began composing the final answer. */
export const synthesisStartDataSchema = z.object({
  from_agents: z.array(z.string()).default([]),
});
export type SynthesisStartData = z.infer<typeof synthesisStartDataSchema>;

/** `steering.accepted` — a mid-run steer was queued (`threads_actions.py::steer_turn`). */
export const steeringAcceptedDataSchema = z.object({ message: z.string() });
export type SteeringAcceptedData = z.infer<typeof steeringAcceptedDataSchema>;

/** `message` — THE final answer, delivered in one shot (there is no token streaming). */
export const messageDataSchema = z.object({
  text: z.string(),
  framework: z.string().optional(),
});
export type MessageData = z.infer<typeof messageDataSchema>;

/** `turn.end` — terminal frame. `status` is the whole outcome; an error rides here, there is no
 * separate `error` event. */
export const turnEndDataSchema = z.object({
  status: z.enum(["done", "cancelled", "error"]),
  message: z.string().nullable().optional(),
});
export type TurnEndData = z.infer<typeof turnEndDataSchema>;
