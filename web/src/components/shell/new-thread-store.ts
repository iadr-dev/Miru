/**
 * Tracks the sidebar's "a brand-new thread is being created right now" moment so `RecentsList`
 * can render the Claude/Gemini-style blank breathing placeholder immediately (before the thread
 * even exists server-side) and then a one-shot typewriter reveal once its auto-generated title
 * lands — without `ChatClient` (the chat feature) and `RecentsList` (the shell) needing a direct
 * prop channel between routes. Zustand per web/CLAUDE.md §3: this is exactly the kind of
 * short-lived cross-surface lifecycle signal that store is for, not scattered React state.
 *
 * Lifecycle: `startPending()` the instant a message is sent with no thread selected → the
 * placeholder shows with no id yet. `resolve(threadId)` once `turn.start` reports the real id
 * (thread now exists server-side, title still empty) → the placeholder swaps for the real row,
 * still breathing. `markRevealed()` once the typewriter finishes painting the generated title →
 * the row goes back to being a plain persisted row and phase returns to idle (never leaving a
 * completed reveal sitting at `"resolved"`, which would otherwise re-satisfy
 * `RecentsList`'s "no thread to reveal yet" placeholder condition and leave a stale breathing row
 * permanently stuck above the real one).
 */
import { create } from "zustand";

type NewThreadPhase = "idle" | "pending" | "resolved";

type NewThreadState = {
  phase: NewThreadPhase;
  threadId: string | null;
  revealed: boolean;
  startPending: () => void;
  resolve: (threadId: string) => void;
  markRevealed: () => void;
  reset: () => void;
};

export const useNewThreadStore = create<NewThreadState>((set) => ({
  phase: "idle",
  threadId: null,
  revealed: false,

  startPending: () => set({ phase: "pending", threadId: null, revealed: false }),

  resolve: (threadId) =>
    set((state) => (state.phase === "idle" ? state : { phase: "resolved", threadId })),

  markRevealed: () => set({ phase: "idle", threadId: null, revealed: false }),

  reset: () => set({ phase: "idle", threadId: null, revealed: false }),
}));
