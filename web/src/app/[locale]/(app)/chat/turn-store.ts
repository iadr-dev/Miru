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
 * There is NO token streaming for the final answer: the assistant text is null through the whole
 * `thinking` phase and appears in one shot when the `message` frame lands. The UI is built around
 * the spawned/progress/done + tool.result lifecycle, not a fake typewriter — EXCEPT for
 * `thinking.delta`, which is a genuine incremental model-reasoning stream (Claude's `thinking_delta`
 * tokens). That one field IS accumulated per-token, keyed by `agent_id` (`thinkingChunks`), since a
 * turn can fan out into multiple lanes and each lane's reasoning is independent.
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
  routeDecidedDataSchema,
  type SseEventKind,
  steeringAcceptedDataSchema,
  thinkingDeltaDataSchema,
  toolResultDataSchema,
  turnEndDataSchema,
  turnStartDataSchema,
  type WebSearchSource,
  webSearchResultDataSchema,
} from "@/types/sse";

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
  const rootEntries = byAgent.get(null);
  if (rootEntries && rootEntries.length > 0) {
    groups.push({ agentId: null, seq: rootEntries[0]?.seq ?? 0, lane: null, entries: rootEntries });
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
  /** The final answer — null until the single `message` frame arrives. */
  answerText: string | null;
  steeringQueued: string[];
  /** Set when `turn.end` carries `status:"error"` (errors ride the terminal frame, no `error` event). */
  errorMessage: string | null;
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
  fail: (message: string) => void;
  clear: () => void;
};

function upsertLane(lanes: LaneView[], next: LaneView): LaneView[] {
  const existing = lanes.find((l) => l.agentId === next.agentId);
  if (existing === undefined) {
    return [...lanes, next];
  }
  // Keep the lane's original `seq` (its first-seen position) — later frames only update
  // status/activity, they must not drag the lane's timeline position forward.
  const updated: LaneView = { ...existing, ...next, seq: existing.seq };
  return lanes.map((lane) => (lane.agentId === next.agentId ? updated : lane));
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
  "tool.result": (base, frame) => {
    const parsed = toolResultDataSchema.safeParse(frame.envelope.data);
    if (!parsed.success) return base;
    return {
      ...base,
      toolSteps: [
        ...base.toolSteps,
        {
          seq: frame.envelope.seq,
          agentId: frame.envelope.agent_id ?? null,
          toolName: parsed.data.tool_name,
          summary: parsed.data.summary ?? null,
        },
      ],
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
  usage: (base) => base,
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
        answerText: null,
        steeringQueued: [],
        errorMessage: null,
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

  fail: (message) =>
    set((state) =>
      state.activeTurn === null
        ? state
        : { activeTurn: { ...state.activeTurn, phase: "error", errorMessage: message } },
    ),

  clear: () => set({ activeTurn: null }),
}));
