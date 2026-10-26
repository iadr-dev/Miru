"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ActiveTurnView } from "@/app/[locale]/(app)/chat/active-turn-view";
import { Composer } from "@/app/[locale]/(app)/chat/composer";
import { EmptyState } from "@/app/[locale]/(app)/chat/empty-state";
import { PersistedThreadView } from "@/app/[locale]/(app)/chat/persisted-thread-view";
import { PetCompanion, type PetNotice } from "@/app/[locale]/(app)/chat/pet-companion";
import {
  type ActiveTurn,
  type ActiveTurnMode,
  useTurnStore,
} from "@/app/[locale]/(app)/chat/turn-store";
import { VoiceModeOverlay } from "@/app/[locale]/(app)/chat/voice-mode-overlay";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { useRouter } from "@/i18n/navigation";
import { fetchMe } from "@/lib/api/account";
import {
  type AgentQueryBody,
  AgentStreamError,
  type AgentStreamFrame,
  type AttachmentInput,
  streamAgentQuery,
  streamEditTurn,
  streamRegenerateTurn,
  streamThreadResume,
} from "@/lib/api/agent-stream";
import { queryKeys } from "@/lib/api/query-keys";
import { streamWithResume } from "@/lib/api/stream-resume";
import { cancelTurn, getThread, patchThread } from "@/lib/api/threads";
import { FOLLOW_ACCOUNT_TONE } from "@/lib/personality";
import type { Thread } from "@/types/conversation";

/** The message shown for a failed turn: a known refusal code gets its localized explanation. */
function turnErrorMessage(err: unknown, t: (key: string) => string): string {
  if (err instanceof AgentStreamError) {
    if (err.code === "chat_limit_reached") {
      return t("chatLimitReached");
    }
    if (err.code === "rate_limited") {
      return t("rateLimited");
    }
    if (err.code === "byok_key_required") {
      return t("byokKeyRequired");
    }
    if (err.code === "feature_not_entitled") {
      return t("deepResearchNotEntitled");
    }
  }
  return err instanceof Error ? err.message : t("error");
}

type StreamOpener = (signal: AbortSignal) => AsyncGenerator<AgentStreamFrame>;

/** The thread id a `turn.start` frame announces, else null. */
function startedThreadId(frame: AgentStreamFrame): string | null {
  if (frame.kind !== "turn.start") {
    return null;
  }
  const id = frame.envelope.data.thread_id;
  return typeof id === "string" ? id : null;
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** Records a `turn.start` thread id: tells the sidebar a brand-new thread now exists. */
function adoptStartedThread(
  frame: AgentStreamFrame,
  current: string | null,
  threadIdAtStart: string | null,
): string | null {
  const started = startedThreadId(frame);
  if (started === null) {
    return current;
  }
  if (started !== threadIdAtStart) {
    useNewThreadStore.getState().resolve(started);
  }
  return started;
}

/** A stream that threw: a duplicate request adopts the thread the server already has, an
 * intentional abort is silent, anything else fails the overlay. Returns the thread id to use. */
function recoverFromStreamError(
  err: unknown,
  resolvedThreadId: string | null,
  fail: (message: string, code?: string | null) => void,
  t: (key: string) => string,
): string | null {
  if (err instanceof AgentStreamError && err.code === "duplicate_request") {
    // The server already has this message (the same request arrived twice). It is not an
    // error: adopt the thread it went to, and the history refetch after this shows it.
    return err.threadId ?? resolvedThreadId;
  }
  if (!isAbortError(err)) {
    // AbortError is an intentional stop (handled by the cancel path) — not a turn error.
    fail(turnErrorMessage(err, t), err instanceof AgentStreamError ? err.code : null);
    useNewThreadStore.getState().reset();
  }
  return resolvedThreadId;
}

/** Reads one stream to its end, folding every frame into the overlay, and returns the thread id
 * the turn resolved to (a brand-new thread's id first surfaces on the `turn.start` frame). */
async function readTurnStream(params: {
  threadIdAtStart: string | null;
  open: StreamOpener;
  signal: AbortSignal;
  applyFrame: (frame: AgentStreamFrame) => void;
  fail: (message: string, code?: string | null) => void;
  t: (key: string) => string;
}): Promise<string | null> {
  const { threadIdAtStart, open, signal, applyFrame, fail, t } = params;
  let resolvedThreadId = threadIdAtStart;
  try {
    // A dropped connection re-attaches to the running turn instead of freezing the answer.
    const frames = streamWithResume(open, streamThreadResume, signal, { threadIdAtStart });
    for await (const frame of frames) {
      applyFrame(frame);
      resolvedThreadId = adoptStartedThread(frame, resolvedThreadId, threadIdAtStart);
    }
  } catch (err) {
    return recoverFromStreamError(err, resolvedThreadId, fail, t);
  }
  return resolvedThreadId;
}

type QueryBodyInput = {
  message: string;
  webSearch: boolean;
  deepResearch: boolean;
  model: string | null;
  personality: string | null;
  threadId: string | null;
  attachments: AttachmentInput[];
};

/** The `POST /agent/query` body: optional fields are omitted, never sent as null. */
function buildQueryBody(input: QueryBodyInput): AgentQueryBody {
  return {
    message: input.message,
    web_search: input.webSearch,
    ...(input.deepResearch ? { deep_research: true } : {}),
    ...(input.model !== null ? { model: input.model } : {}),
    ...(input.personality !== null ? { personality: input.personality } : {}),
    ...(input.threadId ? { thread_id: input.threadId } : {}),
    ...(input.attachments.length > 0 ? { attachments: input.attachments } : {}),
  };
}

/** What the finished turn left in the overlay — read before `clear()` wipes it, the only place the
 * answer text still exists once the persisted-history refetch takes over the render. */
function readFinishedTurn(): {
  answerText: string | null;
  petLeveledTo: number | null;
  petPullsEarned: number;
} {
  const finishedTurn = useTurnStore.getState().activeTurn;
  return {
    answerText: finishedTurn?.phase === "done" ? (finishedTurn.answerText ?? null) : null,
    petLeveledTo: finishedTurn?.petLeveledTo ?? null,
    petPullsEarned: finishedTurn?.petPullsEarned ?? 0,
  };
}

/** The scrolling transcript: empty state, or persisted history plus the live turn. */
function ChatTranscript({
  thread,
  hasThread,
  loadFailed,
  activeTurn,
  streaming,
  onSend,
  onRegenerate,
  onEdit,
}: Readonly<{
  thread: Thread | undefined;
  hasThread: boolean;
  loadFailed: boolean;
  activeTurn: ActiveTurn | null;
  streaming: boolean;
  onSend: (message: string, attachments?: AttachmentInput[]) => void;
  onRegenerate: (assistantTurnId: string, userText: string) => Promise<unknown>;
  onEdit: (userTurnId: string, newText: string) => void;
}>): ReactNode {
  const t = useTranslations("chat.turn");
  if (!hasThread && activeTurn === null) {
    return <EmptyState onPick={onSend} />;
  }
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
      {hasThread && loadFailed ? (
        <p className="text-center text-[var(--text-muted)] text-sm">{t("loadError")}</p>
      ) : null}
      {thread && thread.turns.length > 0 ? (
        <PersistedThreadView
          thread={thread}
          streaming={streaming}
          onRegenerate={onRegenerate}
          onEdit={onEdit}
          onAskUserPick={onSend}
        />
      ) : null}
      {activeTurn !== null ? <ActiveTurnView turn={activeTurn} onAskUserPick={onSend} /> : null}
    </div>
  );
}

/**
 * The Chat page client. Orchestrates: thread selection (URL `?thread=`), the persisted-thread cold
 * read (TanStack `['thread', id]`), and one live turn at a time via the Zustand overlay.
 *
 * Send flow (answers arrive as whole governance-scanned sentences, never token by token —
 * docs/backend/modules/07-conversation-streaming.md): optimistically show the user bubble + enter
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
const PET_NOTICE_MS = 8000;

/** The chat's own 語氣 pick: a pending one wins, and a pending "default" means follow the account
 * (no pick of its own). */
function chosenTone(pending: string | null, persisted: string | null): string | null {
  if (pending === null) {
    return persisted;
  }
  return pending === FOLLOW_ACCOUNT_TONE ? null : pending;
}

export function ChatClient(): ReactNode {
  const t = useTranslations("chat.turn");
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const urlThreadId = searchParams.get("thread");

  const [threadId, setThreadId] = useState<string | null>(urlThreadId);
  const [pendingModelOverride, setPendingModelOverride] = useState<string | null>(null);
  // Same handshake for the per-chat 語氣 pick: held until the thread exists, then sent as
  // `personality` with the first message.
  const [pendingTone, setPendingTone] = useState<string | null>(null);
  // What the equipped pet just earned (a level-up or a pull), shown beside it for a few seconds.
  const [petNotice, setPetNotice] = useState<PetNotice | null>(null);
  const [voiceModeOpen, setVoiceModeOpen] = useState(false);
  // Per-turn "Web search" toggle from the composer's feature menu. Unlike `modelOverride`, this
  // isn't a thread property persisted via `PATCH /threads/{id}` — it's a request-body flag on
  // `/agent/query` itself (`AgentQueryRequest.web_search`), so it lives here as plain local state
  // rather than needing a pending/persisted handshake. Defaults to enabled per product decision
  // (mirrors Claude Code's own default), with the user able to toggle it off per turn.
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  // One-shot "Deep research" (Pro): a cited multi-step research run instead of a normal reply.
  // Each run spends one of the day's reports, so it resets after every send rather than sticking.
  const [deepResearchEnabled, setDeepResearchEnabled] = useState(false);
  const meQuery = useQuery({ queryKey: queryKeys.me, queryFn: ({ signal }) => fetchMe(signal) });
  const canDeepResearch = meQuery.data?.entitlements.features.includes("deep_research") ?? false;
  const { activeTurn, begin, applyFrame, fail, clear } = useTurnStore();
  const abortRef = useRef<AbortController | null>(null);

  // Keep local selection in sync when the URL thread param changes (e.g. 新對話 navigation).
  useEffect(() => {
    setThreadId(urlThreadId);
  }, [urlThreadId]);

  const threadQuery = useQuery({
    queryKey: queryKeys.thread(threadId),
    queryFn: ({ signal }) => getThread(threadId as string, signal),
    enabled: threadId !== null,
  });

  const streaming = activeTurn?.phase === "thinking";

  // Best-effort: a failed PATCH leaves the thread on its previous model (or none) rather than
  // blocking the turn — the picker is a convenience, not a governance-critical path, and the next
  // successful pick/turn will retry the same PATCH shape anyway.
  const applyModelOverride = useCallback(
    async (targetThreadId: string, modelId: string | null): Promise<void> => {
      try {
        const current = await getThread(targetThreadId);
        await patchThread(targetThreadId, { model_override: modelId }, current.version);
        await queryClient.invalidateQueries({ queryKey: queryKeys.thread(targetThreadId) });
      } catch {
        // Swallow — see function docstring above.
      }
    },
    [queryClient],
  );

  // Best-effort like `applyModelOverride`; "default" clears the chat's own pick.
  // Refresh the pet's level/pulls after a turn and say what it earned, then let the note fade.
  const announcePet = useCallback(
    (leveledTo: number | null, pullsEarned: number): void => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("pet-active") });
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("pet-progress") });
      if (leveledTo === null && pullsEarned === 0) {
        return;
      }
      setPetNotice({ leveledTo, pullsEarned });
      window.setTimeout(() => setPetNotice(null), PET_NOTICE_MS);
    },
    [queryClient],
  );

  const applyToneOverride = useCallback(
    async (targetThreadId: string, presetId: string): Promise<void> => {
      try {
        const current = await getThread(targetThreadId);
        await patchThread(targetThreadId, { personality_override: presetId }, current.version);
        await queryClient.invalidateQueries({ queryKey: queryKeys.thread(targetThreadId) });
      } catch {
        // Swallow - the pick is a convenience; the next one retries the same PATCH.
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
      try {
        return await readTurnStream({
          threadIdAtStart,
          open,
          signal: controller.signal,
          applyFrame,
          fail,
          t,
        });
      } finally {
        abortRef.current = null;
      }
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

      // A model picked before this thread existed travelled with the first message (`model` on
      // the query body), so the server already applied and persisted it for that very turn.
      if (pendingModelOverride !== null) {
        setPendingModelOverride(null);
      }
      if (pendingTone !== null) {
        setPendingTone(null);
      }

      // Captured before `clear()` wipes the overlay.
      const { answerText, petLeveledTo, petPullsEarned } = readFinishedTurn();
      announcePet(petLeveledTo, petPullsEarned);

      // Reconcile with persisted history, then drop the live overlay once it lands.
      await queryClient.invalidateQueries({ queryKey: queryKeys.threads });
      if (resolvedThreadId !== null) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.thread(resolvedThreadId) });
      }
      clear();
      return answerText;
    },
    [
      threadId,
      pendingModelOverride,
      pendingTone,
      announcePet,
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
          buildQueryBody({
            message,
            webSearch: webSearchEnabled,
            deepResearch: deepResearchEnabled && canDeepResearch,
            model: pendingModelOverride,
            personality: pendingTone,
            threadId,
            attachments,
          }),
          signal,
        ),
      ),
    [
      runStream,
      webSearchEnabled,
      deepResearchEnabled,
      canDeepResearch,
      pendingModelOverride,
      pendingTone,
      threadId,
    ],
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
      setDeepResearchEnabled(false);
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
    (modelId: string | null): void => {
      if (threadId === null) {
        // Thread doesn't exist yet — hold the pick until `runTurn` learns the real thread id.
        setPendingModelOverride(modelId);
        return;
      }
      void applyModelOverride(threadId, modelId);
    },
    [threadId, applyModelOverride],
  );

  const onToneOverrideChange = useCallback(
    (presetId: string): void => {
      if (threadId === null) {
        setPendingTone(presetId);
        return;
      }
      void applyToneOverride(threadId, presetId);
    },
    [threadId, applyToneOverride],
  );

  const hasThread = threadId !== null;
  const modelOverride = pendingModelOverride ?? threadQuery.data?.model_override ?? null;
  const toneOverride = chosenTone(pendingTone, threadQuery.data?.personality_override ?? null);

  // The model picker exists only for a caller on their own keys (`/me` -> `byok_active`); for
  // everyone else the platform chooses the model and the server ignores any pick.
  const byokActive = meQuery.data?.entitlements.byok_active;

  return (
    <div className="flex h-[calc(100dvh-3.5rem-var(--bottom-nav-h)-env(safe-area-inset-top))] flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ChatTranscript
          thread={threadQuery.data}
          hasThread={hasThread}
          loadFailed={threadQuery.isError}
          activeTurn={activeTurn}
          streaming={streaming}
          onSend={onSend}
          onRegenerate={(assistantTurnId, userText) => runRegenerate(assistantTurnId, userText)}
          onEdit={(userTurnId, newText) => {
            // The edit textarea (`UserBubble`) has no attachment picker of its own — an
            // edited message never adds new attachments, only changes the text.
            void runEdit(userTurnId, newText, []);
          }}
        />
      </div>

      <PetCompanion thinking={streaming} notice={petNotice} />
      <Composer
        streaming={streaming}
        onSend={onSend}
        onStop={onStop}
        modelOverride={modelOverride}
        onModelOverrideChange={onModelOverrideChange}
        toneOverride={toneOverride}
        onToneOverrideChange={onToneOverrideChange}
        webSearchEnabled={webSearchEnabled}
        onWebSearchEnabledChange={setWebSearchEnabled}
        deepResearchLocked={meQuery.data !== undefined && !canDeepResearch}
        byokActive={byokActive}
        deepResearch={
          canDeepResearch
            ? { enabled: deepResearchEnabled, onChange: setDeepResearchEnabled }
            : null
        }
        onOpenVoiceMode={() => setVoiceModeOpen(true)}
      />

      {voiceModeOpen ? (
        <VoiceModeOverlay onClose={() => setVoiceModeOpen(false)} runTurn={runVoiceTurn} />
      ) : null}
    </div>
  );
}
