/**
 * The live-turn Zustand store (web/CLAUDE.md §3: the SSE lifecycle is the one thing that lives in
 * Zustand, never scattered React state, never the TanStack cache).
 *
 * Feature-scoped, so it lives co-located with the chat page rather than in `types/`: no other
 * feature consumes "the current chat turn's in-flight lifecycle". It holds exactly one `activeTurn`
 * overlay — the optimistic user message plus the assistant lifecycle folded from SSE frames. When a
 * turn ends, the page invalidates the persisted-thread query and, once that refetch settles, clears
 * the overlay so the answer is rendered from persisted history (no duplicate, no flicker).
 *
 * The final answer now DOES stream, for `none`/`single`-framework turns: `message.delta` frames
 * accumulate into `streamingAnswerText` sentence-by-sentence as the model generates them (each
 * sentence already governance-scanned server-side before it's ever sent — see
 * `backend/src/agent/loop.py::_stream_step`'s docstring), rendered live while `answerText` is
 * still null. The terminal `message` frame still always carries the complete text in one shot
 * and remains the single source of truth once it lands (`answerText` takes over from
 * `streamingAnswerText` in the render, never reconciled against it) — a `subagent`/`team` turn's
 * synthesis pass never emits `message.delta` at all, so those turns still only ever populate
 * `answerText` in one shot exactly as before (`Synthesizer.synthesize()` uses `chat()`, not
 * `stream()` — a known, intentionally-scoped gap). `thinking.delta` remains the separate genuine
 * incremental model-reasoning stream (Claude's `thinking_delta` tokens), accumulated per-token,
 * keyed by `agent_id` (`thinkingChunks`), since a turn can fan out into multiple lanes and each
 * lane's reasoning is independent.
 */
import { create } from "zustand";
import type { AgentStreamFrame } from "@/lib/api/agent-stream";
import { type RichCard, richCardSchema } from "@/types/conversation";
import {
  agentDoneDataSchema,
  agentProgressDataSchema,
  agentSpawnedDataSchema,
  isSseEventKind,
  messageDataSchema,
  messageDeltaDataSchema,
  routeDecidedDataSchema,
  type SseEventKind,
  steeringAcceptedDataSchema,
  thinkingDeltaDataSchema,
  toolResultDataSchema,
  toolStartDataSchema,
  turnEndDataSchema,
  turnStartDataSchema,
  type WebSearchSource,
  webSearchResultDataSchema,
} from "@/types/sse";
import {
  type MemoryRef,
  memoryUsedDataSchema,
  personalityUsedDataSchema,
  petLeveledDataSchema,
  petPullEarnedDataSchema,
  type TurnUsageData,
  turnUsageDataSchema,
} from "@/types/usage";

export type TurnPhase = "thinking" | "done" | "error" | "cancelled";

export type LaneView = {
  agentId: string;
  /** The envelope `seq` of this lane's FIRST frame — fixes its position among lane groups
   * (`buildLaneGroups`) even though the lane's own status/activity keeps mutating in place. */
  seq: number;
  skill: string | null;
  activity: string | null;
  status: "running" | "done" | "degraded";
};

export type ToolStepView = {
  seq: number;
  /** The lane that made this call — read straight off the SSE envelope (every frame carries
   * `agent_id`), not the `tool.result` payload itself, so this is set even though
   * `toolResultDataSchema`'s own body doesn't carry it. `null` for a root-level (non-fanned-out)
   * `single`/`none` turn, which has no lane to attribute to. */
  agentId: string | null;
  toolName: string;
  summary: string | null;
  /** `"running"` from the moment `tool.start` arrives (no summary yet, since the call hasn't
   * finished); upgraded to `"done"` in place — same array slot, original `seq` — once the
   * matching `tool.result` lands. This is what makes the process disclosure show something
   * DURING a slow tool call rather than only after every call in the Step has finished — the
   * gap `tool.start`/`EventBusPreToolHook` exists to close for models that stream no
   * `thinking_delta` at all. */
  status: "running" | "done";
};

/** One lane's accumulated extended-thinking text (`thinking.delta`, genuinely streamed — see the
 * module docstring). `seq` is pinned to the FIRST delta for that `agent_id` so the chunk keeps a
 * stable timeline position while `text` keeps growing underneath it. */
export type ThinkingChunkView = {
  agentId: string;
  seq: number;
  text: string;
};

/** One entry in a lane's own sub-timeline — tool steps and thinking chunks belonging to that
 * lane, seq-ordered. */
export type LaneSubEntry =
  | { kind: "tool"; seq: number; step: ToolStepView }
  | { kind: "thinking"; seq: number; chunk: ThinkingChunkView };

/** One lane and everything it produced, grouped for `ProcessDisclosure`'s nested render — replaces
 * the old flat interleaved timeline (every tool/thinking entry mixed together regardless of which
 * lane produced it, which made `subagent`'s parallel lanes and `team`'s sequential ones
 * indistinguishable). `agentId: null` covers a root-level `single`/`none` turn with no fan-out —
 * its tool/thinking entries have no lane header, rendered as one implicit ungrouped bucket. */
export type LaneGroup = {
  agentId: string | null;
  seq: number;
  lane: LaneView | null;
  entries: LaneSubEntry[];
};

/** Group `toolSteps`/`thinkingChunks` by their owning `agentId` under each `lane`, each group's
 * own entries kept in `seq` order — the nested-by-lane structure `ProcessDisclosure` renders. A
 * plain function (not store state) since it's a pure derivation of the three lists already held.
 * Groups themselves are ordered by the owning lane's `seq` (its first-seen position); the
 * root-level (`agentId: null`) bucket, if non-empty, sorts by its own first entry's `seq`. */
export function buildLaneGroups(turn: ActiveTurn): LaneGroup[] {
  const byAgent = new Map<string | null, LaneSubEntry[]>();
  const pushEntry = (agentId: string | null, entry: LaneSubEntry): void => {
    const existing = byAgent.get(agentId);
    if (existing) {
      existing.push(entry);
    } else {
      byAgent.set(agentId, [entry]);
    }
  };
  for (const step of turn.toolSteps) {
    pushEntry(step.agentId, { kind: "tool", seq: step.seq, step });
  }
  for (const chunk of turn.thinkingChunks) {
    pushEntry(chunk.agentId, { kind: "thinking", seq: chunk.seq, chunk });
  }
  for (const entries of byAgent.values()) {
    entries.sort((a, b) => a.seq - b.seq);
  }

  const groups: LaneGroup[] = turn.lanes.map((lane) => ({
    agentId: lane.agentId,
    seq: lane.seq,
    lane,
    entries: byAgent.get(lane.agentId) ?? [],
  }));
  for (const lane of turn.lanes) {
    byAgent.delete(lane.agentId);
  }
  // Whatever's left is un-lane'd — a root-level `single`/`none` turn's entries (the backend
  // always stamps `agent_id: "root"` on these, never a literal `null`, even with no
  // `agent.spawned` fan-out — see `orchestrator.py::_ROOT_AGENT_ID`) plus any entry whose
  // envelope genuinely carried no `agent_id` at all. Both render as one implicit ungrouped
  // bucket; a turn never has both, since a fanned-out turn's root loop doesn't itself call
  // tools.
  const unLaned = [...byAgent.values()].flat();
  if (unLaned.length > 0) {
    unLaned.sort((a, b) => a.seq - b.seq);
    groups.push({ agentId: null, seq: unLaned[0]?.seq ?? 0, lane: null, entries: unLaned });
  }
  return groups.sort((a, b) => a.seq - b.seq);
}

/** A live rich card keyed by the envelope `seq` (globally monotonic per turn) for a stable React
 * key — cards are append-only, so `seq` is unique and order-stable. */
export type RichCardView = {
  seq: number;
  card: RichCard;
};

/** `"send"` is a fresh/edited user message — the optimistic user bubble renders at the top of the
 * overlay. `"regenerate"` re-runs an EXISTING (already-rendered-in-history) user turn, so the
 * overlay renders only the assistant lifecycle/answer, with no duplicate user bubble. */
export type ActiveTurnMode = "send" | "regenerate";

export type ActiveTurn = {
  /** Resolved from `turn.start` (a new thread's id first surfaces here) / the envelope. */
  threadId: string | null;
  /** The persisted turn id (from the envelope) — needed to cancel a running turn. */
  turnId: string | null;
  mode: ActiveTurnMode;
  userText: string;
  phase: TurnPhase;
  framework: string | null;
  lanes: LaneView[];
  toolSteps: ToolStepView[];
  thinkingChunks: ThinkingChunkView[];
  richCards: RichCardView[];
  /** Accumulated across every `web_search.result` frame this turn (one per native-web-search
   * call, e.g. a fan-out with several lanes each searching) — append-only, never deduped, since
   * the "N sources" chip counts every source actually consulted, even repeats across lanes. */
  webSearchSources: WebSearchSource[];
  /** The latest `usage` frame (the backend re-sends running totals) - null until one arrives. */
  usage: TurnUsageData | null;
  /** The long-term memories recall put in front of the model this turn (from `memory.used`). */
  memoriesUsed: MemoryRef[];
  /** The 語氣 preset id that styled this turn (from `personality.used`), null until it arrives. */
  personality: string | null;
  /** What the equipped pet earned this turn (from `pet.leveled` / `pet.pull_earned`). */
  petLeveledTo: number | null;
  petPullsEarned: number;
  /** The final answer, in one shot — null until the terminal `message` frame arrives. Always
   * the authoritative full text once set (see `streamingAnswerText` for the live-partial
   * counterpart); a client that missed every `message.delta` still gets the complete answer
   * here. */
  answerText: string | null;
  /** The live-streamed partial answer, accumulated sentence-by-sentence from `message.delta`
   * frames — null until the first delta arrives, then grows until the turn ends. Rendered ONLY
   * while `answerText` is still null (see `ActiveTurnView`): once the terminal `message` frame
   * lands, `answerText` takes over as the single source of truth and this is left stale/unused,
   * never reconciled against it — the two are expected to be textually identical for a
   * `none`/`single` turn and there is nothing productive to do if they ever weren't (a
   * `subagent`/`team` turn never populates this at all — see the module docstring). */
  streamingAnswerText: string | null;
  steeringQueued: string[];
  /** Set when `turn.end` carries `status:"error"` (errors ride the terminal frame, no `error` event). */
  errorMessage: string | null;
  /** The backend's stable error code when the turn was refused (a limit, a missing plan...). */
  errorCode: string | null;
};

type TurnStore = {
  activeTurn: ActiveTurn | null;
  /** Begin a turn optimistically — the user bubble shows immediately (mode `"send"`), phase
   * enters `thinking`. `mode` defaults to `"send"`; pass `"regenerate"` when re-running an
   * already-persisted user turn (see `ActiveTurnMode`). */
  begin: (userText: string, threadId: string | null, mode?: ActiveTurnMode) => void;
  /** Fold one SSE frame into the active turn. */
  applyFrame: (frame: AgentStreamFrame) => void;
  /** A transport-level failure (the stream threw before a terminal frame) → calm error phase. */
  fail: (message: string, code?: string | null) => void;
  clear: () => void;
};

function upsertLane(lanes: LaneView[], next: LaneView): LaneView[] {
  const existing = lanes.find((l) => l.agentId === next.agentId);
  if (existing === undefined) {
    return [...lanes, next];
  }
  // Keep the lane's original `seq` (its first-seen position) — later frames only update
  // status/activity, they must not drag the lane's timeline position forward. `skill`/`activity`
  // arrive on `agent.spawned` only; a later `agent.progress`/`agent.done` carries null for both and
  // must not erase them, or a finished lane falls back to showing its raw agent id.
  const updated: LaneView = {
    ...existing,
    ...next,
    seq: existing.seq,
    skill: next.skill ?? existing.skill,
    activity: next.activity ?? existing.activity,
  };
  return lanes.map((lane) => (lane.agentId === next.agentId ? updated : lane));
}

/** `tool.start` always appends a new `"running"` step — it never matches an existing one
 * (each call gets its own fresh row the instant it's about to dispatch). */
function appendToolStart(steps: ToolStepView[], next: ToolStepView): ToolStepView[] {
  return [...steps, next];
}

/** `tool.result` upgrades the OLDEST still-`"running"` step for the same `(agentId, toolName)`
 * pair in place (same array slot, same `seq`) rather than appending a second row — the schemas
 * carry no shared call id, but within one lane the same tool name is dispatched and resolved in
 * FIFO order (`AgentLoop.run_turn` awaits each call before starting the next), so oldest-running
 * is an exact match, not a heuristic. Falls back to appending (the pre-`tool.start` behavior) if
 * no running placeholder exists — a `tool.start` frame dropped by the transport, or a persisted/
 * replayed stream that never sent one, must still render the completed step. */
function upsertToolResult(
  steps: ToolStepView[],
  next: Omit<ToolStepView, "status">,
): ToolStepView[] {
  const running = steps.find(
    (s) => s.status === "running" && s.agentId === next.agentId && s.toolName === next.toolName,
  );
  if (running === undefined) {
    return [...steps, { ...next, status: "done" }];
  }
  return steps.map((s) => (s === running ? { ...next, seq: running.seq, status: "done" } : s));
}

function appendThinkingDelta(
  chunks: ThinkingChunkView[],
  agentId: string,
  text: string,
  seq: number,
): ThinkingChunkView[] {
  const existing = chunks.find((c) => c.agentId === agentId);
  if (existing === undefined) {
    return [...chunks, { agentId, seq, text }];
  }
  const updated: ThinkingChunkView = { ...existing, text: existing.text + text };
  return chunks.map((chunk) => (chunk.agentId === agentId ? updated : chunk));
}

/** One per-`frame.kind` reducer: `base` already has `turnId` resolved; each function narrows
 * `frame.envelope.data` against its own schema and returns the next `ActiveTurn` (or `base`
 * unchanged on a parse failure — an unrecognized/malformed payload never crashes the overlay).
 * Split out of `applyFrame`'s switch purely to keep that function's own branching shallow — each
 * kind becomes a one-line dispatch instead of an inline schema-parse-and-merge block. Keyed by
 * EVERY `SseEventKind` (a total, not partial, map) so a newly-emitted kind added to
 * `SSE_EVENT_KINDS` is a compile error here until given a reducer — `synthesis.start`/`usage`
 * carry no overlay-relevant fields, so their reducer is the identity function. */
const FRAME_REDUCERS: {
  [K in SseEventKind]: (base: ActiveTurn, frame: AgentStreamFrame) => ActiveTurn;
} = {
  "turn.start": (base, frame) => {
    const parsed = turnStartDataSchema.safeParse(frame.envelope.data);
    return { ...base, threadId: parsed.success ? parsed.data.thread_id : base.threadId };
  },
  "route.decided": (base, frame) => {
    const parsed = routeDecidedDataSchema.safeParse(frame.envelope.data);
    return { ...base, framework: parsed.success ? parsed.data.framework : base.framework };
  },
  "agent.spawned": (base, frame) => {
    const parsed = agentSpawnedDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      lanes: upsertLane(base.lanes, {
        agentId: parsed.data.agent_id,
        seq: frame.envelope.seq,
        skill: parsed.data.skill ?? null,
        activity: parsed.data.activity ?? null,
        status: "running",
      }),
    };
  },
  "agent.progress": (base, frame) => {
    const parsed = agentProgressDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    const degraded = parsed.data.status === "degraded";
    return {
      ...base,
      lanes: upsertLane(base.lanes, {
        agentId: parsed.data.agent_id,
        seq: frame.envelope.seq,
        skill: null,
        activity: null,
        status: degraded ? "degraded" : "running",
      }),
    };
  },
  "agent.done": (base, frame) => {
    const parsed = agentDoneDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      lanes: upsertLane(base.lanes, {
        agentId: parsed.data.agent_id,
        seq: frame.envelope.seq,
        skill: null,
        activity: null,
        status: "done",
      }),
    };
  },
  "tool.start": (base, frame) => {
    const parsed = toolStartDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      toolSteps: appendToolStart(base.toolSteps, {
        seq: frame.envelope.seq,
        agentId: frame.envelope.agent_id ?? null,
        toolName: parsed.data.tool_name,
        summary: null,
        status: "running",
      }),
    };
  },
  "tool.result": (base, frame) => {
    const parsed = toolResultDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      toolSteps: upsertToolResult(base.toolSteps, {
        seq: frame.envelope.seq,
        agentId: frame.envelope.agent_id ?? null,
        toolName: parsed.data.tool_name,
        summary: parsed.data.summary ?? null,
      }),
    };
  },
  "thinking.delta": (base, frame) => {
    const parsed = thinkingDeltaDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      thinkingChunks: appendThinkingDelta(
        base.thinkingChunks,
        parsed.data.agent_id,
        parsed.data.text,
        frame.envelope.seq,
      ),
    };
  },
  "web_search.result": (base, frame) => {
    const parsed = webSearchResultDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return { ...base, webSearchSources: [...base.webSearchSources, ...parsed.data.sources] };
  },
  rich_card: (base, frame) => {
    const parsed = richCardSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      richCards: [...base.richCards, { seq: frame.envelope.seq, card: parsed.data }],
    };
  },
  "steering.accepted": (base, frame) => {
    const parsed = steeringAcceptedDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return { ...base, steeringQueued: [...base.steeringQueued, parsed.data.message] };
  },
  "message.delta": (base, frame) => {
    const parsed = messageDeltaDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      streamingAnswerText: (base.streamingAnswerText ?? "") + parsed.data.text,
    };
  },
  message: (base, frame) => {
    const parsed = messageDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      answerText: parsed.data.text,
      framework: parsed.data.framework ?? base.framework,
    };
  },
  "turn.end": (base, frame) => {
    const parsed = turnEndDataSchema.safeParse(frame.envelope.data);
    const status = parsed.success ? parsed.data.status : "done";
    const phase: TurnPhase =
      status === "error" ? "error" : status === "cancelled" ? "cancelled" : "done";
    return {
      ...base,
      phase,
      errorMessage:
        status === "error" ? ((parsed.success ? parsed.data.message : null) ?? "") : null,
    };
  },
  "synthesis.start": (base) => base,
  "memory.used": (base, frame) => {
    const parsed = memoryUsedDataSchema.safeParse(frame.envelope.data);
    return parsed.success ? { ...base, memoriesUsed: parsed.data.memories } : base;
  },
  "pet.leveled": (base, frame) => {
    const parsed = petLeveledDataSchema.safeParse(frame.envelope.data);
    return parsed.success ? { ...base, petLeveledTo: parsed.data.level } : base;
  },
  "pet.pull_earned": (base, frame) => {
    const parsed = petPullEarnedDataSchema.safeParse(frame.envelope.data);
    return parsed.success ? { ...base, petPullsEarned: parsed.data.count } : base;
  },
  "personality.used": (base, frame) => {
    const parsed = personalityUsedDataSchema.safeParse(frame.envelope.data);
    return parsed.success ? { ...base, personality: parsed.data.personality } : base;
  },
  usage: (base, frame) => {
    const parsed = turnUsageDataSchema.safeParse(frame.envelope.data);
    return parsed.success ? { ...base, usage: parsed.data } : base;
  },
};

export const useTurnStore = create<TurnStore>((set) => ({
  activeTurn: null,

  begin: (userText, threadId, mode = "send") =>
    set({
      activeTurn: {
        threadId,
        turnId: null,
        mode,
        userText,
        phase: "thinking",
        framework: null,
        lanes: [],
        toolSteps: [],
        thinkingChunks: [],
        richCards: [],
        webSearchSources: [],
        usage: null,
        memoriesUsed: [],
        personality: null,
        petLeveledTo: null,
        petPullsEarned: 0,
        answerText: null,
        streamingAnswerText: null,
        steeringQueued: [],
        errorMessage: null,
        errorCode: null,
      },
    }),

  applyFrame: (frame) =>
    set((state) => {
      const turn = state.activeTurn;
      if (turn === null) {
        return state;
      }
      // Every envelope carries the turn id — capture it once so cancel has a target.
      const turnId = turn.turnId ?? frame.envelope.turn_id ?? null;
      const base: ActiveTurn = { ...turn, turnId };

      if (!isSseEventKind(frame.kind)) {
        return { activeTurn: base }; // unknown/future kind — ignore, don't crash
      }

      return { activeTurn: FRAME_REDUCERS[frame.kind](base, frame) };
    }),

  fail: (message, code = null) =>
    set((state) =>
      state.activeTurn === null
        ? state
        : {
            activeTurn: {
              ...state.activeTurn,
              phase: "error",
              errorMessage: message,
              errorCode: code,
            },
          },
    ),

  clear: () => set({ activeTurn: null }),
}));
