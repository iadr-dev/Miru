"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ActiveTurnView } from "@/app/[locale]/(app)/chat/active-turn-view";
import { Composer } from "@/app/[locale]/(app)/chat/composer";
import { EmptyState } from "@/app/[locale]/(app)/chat/empty-state";
import { PersistedThreadView } from "@/app/[locale]/(app)/chat/persisted-thread-view";
import { type ActiveTurnMode, useTurnStore } from "@/app/[locale]/(app)/chat/turn-store";
import { VoiceModeOverlay } from "@/app/[locale]/(app)/chat/voice-mode-overlay";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { useRouter } from "@/i18n/navigation";
import {
  type AgentStreamFrame,
  type AttachmentInput,
  streamAgentQuery,
  streamEditTurn,
  streamRegenerateTurn,
} from "@/lib/api/agent-stream";
import { cancelTurn, getThread, patchThread } from "@/lib/api/threads";

/**
 * The Chat page client. Orchestrates: thread selection (URL `?thread=`), the persisted-thread cold
 * read (TanStack `['thread', id]`), and one live turn at a time via the Zustand overlay.
 *
 * Send flow (no token streaming — DEVELOPMENT.md §4b): optimistically show the user bubble + enter
 * the thinking phase (`begin`), open the `POST /agent/query` SSE stream, fold each frame into the
 * store (`applyFrame`). On the terminal `turn.end`, invalidate the thread + thread-list queries;
 * once the persisted refetch settles the overlay is cleared so the answer renders from history (no
 * duplicate). The composer's stop control POSTs cancel then aborts the stream.
 *
 * Model override handshake: the composer's model picker is controlled from here, not local state,
 * because persisting a pick means `PATCH /threads/{id}` — which needs a thread that may not exist
 * yet (a fresh `/chat` with no message sent). A pick before the first send is held in
 * `pendingModelOverride` and applied the moment `runTurn` learns the new thread's real id; a pick
 * once a thread already exists PATCHes immediately. Either way the two branches share one
 * `applyModelOverride` so the persisted value and the query-cache invalidation stay in sync.
 */
export function ChatClient(): ReactNode {
  const t = useTranslations("chat.turn");
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const urlThreadId = searchParams.get("thread");

  const [threadId, setThreadId] = useState<string | null>(urlThreadId);
  const [pendingModelOverride, setPendingModelOverride] = useState<string | null>(null);
  const [voiceModeOpen, setVoiceModeOpen] = useState(false);
  // Per-turn "Web search" toggle from the composer's feature menu. Unlike `modelOverride`, this
  // isn't a thread property persisted via `PATCH /threads/{id}` — it's a request-body flag on
  // `/agent/query` itself (`AgentQueryRequest.web_search`), so it lives here as plain local state
  // rather than needing a pending/persisted handshake. Defaults to enabled per product decision
  // (mirrors Claude Code's own default), with the user able to toggle it off per turn.
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const { activeTurn, begin, applyFrame, fail, clear } = useTurnStore();
  const abortRef = useRef<AbortController | null>(null);

  // Keep local selection in sync when the URL thread param changes (e.g. 新對話 navigation).
  useEffect(() => {
    setThreadId(urlThreadId);
  }, [urlThreadId]);

  const threadQuery = useQuery({
    queryKey: ["thread", threadId],
    queryFn: ({ signal }) => getThread(threadId as string, signal),
    enabled: threadId !== null,
  });

  const streaming = activeTurn !== null && activeTurn.phase === "thinking";

  // Best-effort: a failed PATCH leaves the thread on its previous model (or none) rather than
  // blocking the turn — the picker is a convenience, not a governance-critical path, and the next
  // successful pick/turn will retry the same PATCH shape anyway.
  const applyModelOverride = useCallback(
    async (targetThreadId: string, modelId: string): Promise<void> => {
      try {
        const current = await getThread(targetThreadId);
        await patchThread(targetThreadId, { model_override: modelId }, current.version);
        await queryClient.invalidateQueries({ queryKey: ["thread", targetThreadId] });
      } catch {
        // Swallow — see function docstring above.
      }
    },
    [queryClient],
  );

  // Consumes one stream, folding every frame into the overlay, and returns the thread id the
  // turn resolved to (a brand-new thread's id first surfaces on the `turn.start` frame). Split
  // out of `runStream` purely to keep that function's own branching shallow.
  const consumeStream = useCallback(
    async (
      threadIdAtStart: string | null,
      open: (signal: AbortSignal) => AsyncGenerator<AgentStreamFrame>,
    ): Promise<string | null> => {
      const controller = new AbortController();
      abortRef.current = controller;
      let resolvedThreadId = threadIdAtStart;
      try {
        for await (const frame of open(controller.signal)) {
          applyFrame(frame);
          if (frame.kind === "turn.start" && typeof frame.envelope.data.thread_id === "string") {
            resolvedThreadId = frame.envelope.data.thread_id;
            if (resolvedThreadId !== threadIdAtStart) {
              useNewThreadStore.getState().resolve(resolvedThreadId);
            }
          }
        }
      } catch (err) {
        // AbortError is an intentional stop (handled by the cancel path) — not a turn error.
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          fail(err instanceof Error ? err.message : t("error"));
          useNewThreadStore.getState().reset();
        }
      } finally {
        abortRef.current = null;
      }
      return resolvedThreadId;
    },
    [applyFrame, fail, t],
  );

  // Shared by a fresh send, a regenerate, an edit-resubmit, and a voice-mode turn — all four open
  // a `text/event-stream` POST and fold identical frame kinds into the same overlay; only which
  // endpoint is opened (and whether an optimistic user bubble is shown) differs, so that part is
  // injected as `open` while this owns the frame loop, thread-id adoption, and cache
  // reconciliation. Returns the turn's final answer text (or `null` on error/cancellation) so
  // voice mode — which has no visible transcript to read back, only a spoken answer — can pass it
  // straight to TTS; typed sends simply ignore the return value.
  const runStream = useCallback(
    async (
      userText: string,
      mode: ActiveTurnMode,
      open: (signal: AbortSignal) => AsyncGenerator<AgentStreamFrame>,
    ): Promise<string | null> => {
      begin(userText, threadId, mode);
      // No thread selected yet — this send is about to create one. Flag it so the sidebar shows
      // the breathing placeholder immediately, before the thread even exists server-side.
      if (threadId === null && mode === "send") {
        useNewThreadStore.getState().startPending();
      }

      const resolvedThreadId = await consumeStream(threadId, open);

      // A brand-new thread's id first surfaces on turn.start — adopt it so the URL + queries track it.
      if (resolvedThreadId !== null && resolvedThreadId !== threadId) {
        setThreadId(resolvedThreadId);
        router.replace(`/chat?thread=${encodeURIComponent(resolvedThreadId)}`);
      }

      // A model picked before this thread existed is applied now that it has a real id — the
      // turn that just ran used whatever the thread's prior/default model was (there was no
      // thread to PATCH yet), so the override takes effect starting with the *next* turn.
      if (resolvedThreadId !== null && pendingModelOverride !== null) {
        await applyModelOverride(resolvedThreadId, pendingModelOverride);
        setPendingModelOverride(null);
      }

      // Captured before `clear()` wipes the overlay — the only place the finished turn's answer
      // text still exists once the persisted-history refetch below takes over the render.
      const finishedTurn = useTurnStore.getState().activeTurn;
      const answerText = finishedTurn?.phase === "done" ? (finishedTurn.answerText ?? null) : null;

      // Reconcile with persisted history, then drop the live overlay once it lands.
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      if (resolvedThreadId !== null) {
        await queryClient.invalidateQueries({ queryKey: ["thread", resolvedThreadId] });
      }
      clear();
      return answerText;
    },
    [
      threadId,
      pendingModelOverride,
      applyModelOverride,
      begin,
      consumeStream,
      clear,
      queryClient,
      router,
    ],
  );

  const runTurn = useCallback(
    (message: string, attachments: AttachmentInput[]): Promise<string | null> =>
      runStream(message, "send", (signal) =>
        streamAgentQuery(
          {
            message,
            web_search: webSearchEnabled,
            ...(threadId ? { thread_id: threadId } : {}),
            ...(attachments.length > 0 ? { attachments } : {}),
          },
          signal,
        ),
      ),
    [runStream, webSearchEnabled, threadId],
  );

  // Voice mode's own entrypoint: identical to a typed send (`runTurn`), but the caller wants the
  // final answer text back to speak it — never called while a turn is already streaming (the
  // overlay's own state machine gates on this the same way the composer's `streaming` prop does).
  const runVoiceTurn = useCallback(
    (message: string): Promise<string | null> => {
      if (streaming) {
        return Promise.resolve(null);
      }
      return runTurn(message, []);
    },
    [streaming, runTurn],
  );

  // Regenerate/edit both need a real thread — neither can fire before a first send creates one,
  // which the action rows below only render for persisted (already-thread-scoped) turns anyway.
  // Regenerate re-runs the ORIGINAL user turn verbatim (no new attachments to send — the source
  // turn's own attachments, if any, are re-derived server-side same as its text is).
  const runRegenerate = useCallback(
    (assistantTurnId: string, userText: string): Promise<string | null> => {
      if (threadId === null) {
        return Promise.resolve(null);
      }
      return runStream(userText, "regenerate", (signal) =>
        streamRegenerateTurn(threadId, assistantTurnId, { web_search: webSearchEnabled }, signal),
      );
    },
    [runStream, webSearchEnabled, threadId],
  );

  const runEdit = useCallback(
    (
      userTurnId: string,
      newText: string,
      attachments: AttachmentInput[],
    ): Promise<string | null> => {
      if (threadId === null) {
        return Promise.resolve(null);
      }
      return runStream(newText, "send", (signal) =>
        streamEditTurn(
          threadId,
          userTurnId,
          {
            message: newText,
            web_search: webSearchEnabled,
            ...(attachments.length > 0 ? { attachments } : {}),
          },
          signal,
        ),
      );
    },
    [runStream, webSearchEnabled, threadId],
  );

  const onSend = useCallback(
    (message: string, attachments: AttachmentInput[] = []): void => {
      if (streaming) {
        return;
      }
      void runTurn(message, attachments);
    },
    [streaming, runTurn],
  );

  const onStop = useCallback((): void => {
    const turn = useTurnStore.getState().activeTurn;
    if (turn?.threadId && turn.turnId) {
      void cancelTurn(turn.threadId, turn.turnId);
    }
    abortRef.current?.abort();
  }, []);

  const onModelOverrideChange = useCallback(
    (modelId: string): void => {
      if (threadId === null) {
        // Thread doesn't exist yet — hold the pick until `runTurn` learns the real thread id.
        setPendingModelOverride(modelId);
        return;
      }
      void applyModelOverride(threadId, modelId);
    },
    [threadId, applyModelOverride],
  );

  const hasThread = threadId !== null;
  const hasHistory = threadQuery.data && threadQuery.data.turns.length > 0;
  const showEmpty = !hasThread && activeTurn === null;
  const modelOverride = pendingModelOverride ?? threadQuery.data?.model_override ?? null;

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {showEmpty ? (
          <EmptyState onPick={onSend} />
        ) : (
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
            {hasThread && threadQuery.isError ? (
              <p className="text-center text-[var(--text-muted)] text-sm">{t("loadError")}</p>
            ) : null}
            {hasHistory ? (
              <PersistedThreadView
                thread={threadQuery.data}
                streaming={streaming}
                onRegenerate={(assistantTurnId, userText) =>
                  runRegenerate(assistantTurnId, userText)
                }
                onEdit={(userTurnId, newText) => {
                  // The edit textarea (`UserBubble`) has no attachment picker of its own — an
                  // edited message never adds new attachments, only changes the text.
                  void runEdit(userTurnId, newText, []);
                }}
                onAskUserPick={onSend}
              />
            ) : null}
            {activeTurn !== null ? (
              <ActiveTurnView turn={activeTurn} onAskUserPick={onSend} />
            ) : null}
          </div>
        )}
      </div>

      <Composer
        streaming={streaming}
        onSend={onSend}
        onStop={onStop}
        modelOverride={modelOverride}
        onModelOverrideChange={onModelOverrideChange}
        webSearchEnabled={webSearchEnabled}
        onWebSearchEnabledChange={setWebSearchEnabled}
        onOpenVoiceMode={() => setVoiceModeOpen(true)}
      />

      {voiceModeOpen ? (
        <VoiceModeOverlay onClose={() => setVoiceModeOpen(false)} runTurn={runVoiceTurn} />
      ) : null}
    </div>
  );
}
