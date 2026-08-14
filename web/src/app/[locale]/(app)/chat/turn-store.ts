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
  /** The envelope `seq` of this lane's FIRST frame — fixes its position in the merged timeline
   * (`buildTimeline`) even though the lane's own status/activity keeps mutating in place. */
  seq: number;
  skill: string | null;
  activity: string | null;
  status: "running" | "done" | "degraded";
};

export type ToolStepView = {
  seq: number;
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

/** One entry in the merged, seq-ordered timeline `ProcessDisclosure` renders — lanes, tool steps,
 * and thinking chunks interleaved chronologically instead of three separate lists. */
export type TimelineEntry =
  | { kind: "lane"; seq: number; lane: LaneView }
  | { kind: "tool"; seq: number; step: ToolStepView }
  | { kind: "thinking"; seq: number; chunk: ThinkingChunkView };

/** Merge `lanes`/`toolSteps`/`thinkingChunks` into one seq-ordered array for the timeline UI. A
 * plain function (not store state) since it's a pure derivation of the three lists already held. */
export function buildTimeline(turn: ActiveTurn): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    ...turn.lanes.map((lane): TimelineEntry => ({ kind: "lane", seq: lane.seq, lane })),
    ...turn.toolSteps.map((step): TimelineEntry => ({ kind: "tool", seq: step.seq, step })),
    ...turn.thinkingChunks.map(
      (chunk): TimelineEntry => ({ kind: "thinking", seq: chunk.seq, chunk }),
    ),
  ];
  return entries.sort((a, b) => a.seq - b.seq);
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

      const { data } = frame.envelope;
      switch (frame.kind) {
        case "turn.start": {
          const parsed = turnStartDataSchema.safeParse(data);
          return {
            activeTurn: {
              ...base,
              threadId: parsed.success ? parsed.data.thread_id : base.threadId,
            },
          };
        }
        case "route.decided": {
          const parsed = routeDecidedDataSchema.safeParse(data);
          return {
            activeTurn: {
              ...base,
              framework: parsed.success ? parsed.data.framework : base.framework,
            },
          };
        }
        case "agent.spawned": {
          const parsed = agentSpawnedDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              lanes: upsertLane(base.lanes, {
                agentId: parsed.data.agent_id,
                seq: frame.envelope.seq,
                skill: parsed.data.skill ?? null,
                activity: parsed.data.activity ?? null,
                status: "running",
              }),
            },
          };
        }
        case "agent.progress": {
          const parsed = agentProgressDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          const degraded = parsed.data.status === "degraded";
          return {
            activeTurn: {
              ...base,
              lanes: upsertLane(base.lanes, {
                agentId: parsed.data.agent_id,
                seq: frame.envelope.seq,
                skill: null,
                activity: null,
                status: degraded ? "degraded" : "running",
              }),
            },
          };
        }
        case "agent.done": {
          const parsed = agentDoneDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              lanes: upsertLane(base.lanes, {
                agentId: parsed.data.agent_id,
                seq: frame.envelope.seq,
                skill: null,
                activity: null,
                status: "done",
              }),
            },
          };
        }
        case "tool.result": {
          const parsed = toolResultDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              toolSteps: [
                ...base.toolSteps,
                {
                  seq: frame.envelope.seq,
                  toolName: parsed.data.tool_name,
                  summary: parsed.data.summary ?? null,
                },
              ],
            },
          };
        }
        case "thinking.delta": {
          const parsed = thinkingDeltaDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              thinkingChunks: appendThinkingDelta(
                base.thinkingChunks,
                parsed.data.agent_id,
                parsed.data.text,
                frame.envelope.seq,
              ),
            },
          };
        }
        case "web_search.result": {
          const parsed = webSearchResultDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              webSearchSources: [...base.webSearchSources, ...parsed.data.sources],
            },
          };
        }
        case "rich_card": {
          const parsed = richCardSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              richCards: [...base.richCards, { seq: frame.envelope.seq, card: parsed.data }],
            },
          };
        }
        case "steering.accepted": {
          const parsed = steeringAcceptedDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              steeringQueued: [...base.steeringQueued, parsed.data.message],
            },
          };
        }
        case "message": {
          const parsed = messageDataSchema.safeParse(data);
          if (!parsed.success) {
            return { activeTurn: base };
          }
          return {
            activeTurn: {
              ...base,
              answerText: parsed.data.text,
              framework: parsed.data.framework ?? base.framework,
            },
          };
        }
        case "turn.end": {
          const parsed = turnEndDataSchema.safeParse(data);
          const status = parsed.success ? parsed.data.status : "done";
          const phase: TurnPhase =
            status === "error" ? "error" : status === "cancelled" ? "cancelled" : "done";
          return {
            activeTurn: {
              ...base,
              phase,
              errorMessage:
                status === "error" ? ((parsed.success ? parsed.data.message : null) ?? "") : null,
            },
          };
        }
        case "synthesis.start":
        case "usage":
          return { activeTurn: base };
        default:
          return assertNever(frame.kind);
      }
    }),

  fail: (message) =>
    set((state) =>
      state.activeTurn === null
        ? state
        : { activeTurn: { ...state.activeTurn, phase: "error", errorMessage: message } },
    ),

  clear: () => set({ activeTurn: null }),
}));

/** Exhaustiveness guard — a newly-emitted SSE kind added to `SSE_EVENT_KINDS` becomes a compile
 * error here until its reducer arm is written (web/CLAUDE.md §2.1 discriminated-union rule). */
function assertNever(value: never): never {
  throw new Error(`Unhandled SSE event kind: ${String(value)}`);
}
