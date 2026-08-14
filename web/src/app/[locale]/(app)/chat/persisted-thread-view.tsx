"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { AssistantActions, UserBubble } from "@/app/[locale]/(app)/chat/active-turn-view";
import { AnswerMarkdown } from "@/app/[locale]/(app)/chat/answer-markdown";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { RichCardView } from "@/app/[locale]/(app)/chat/rich-card";
import { WebSearchSources } from "@/app/[locale]/(app)/chat/web-search-sources";
import { type FeedbackRating, submitTurnFeedback } from "@/lib/api/threads";
import {
  richCardSchema,
  type Thread,
  type Turn,
  webSearchSourcesBlockSchema,
} from "@/types/conversation";

/**
 * Renders a loaded thread's persisted history (the TanStack `['thread', id]` cold read). Maps each
 * turn's message blocks to the view: `text` blocks render as user bubble (user turn) or document
 * answer (assistant turn); `rich_card` blocks render inline; a `caveat` block renders the pinned
 * chip. Assistant answers always get the required non-trading caveat even if the persisted turn
 * predates a stored caveat block (governance: the caveat is non-removable).
 *
 * Block shapes mirror `backend/src/conversation/models.py`; `content` is an open record narrowed
 * per block `type` here (parse-don't-trust on the fields we read).
 *
 * Every persisted turn — the bulk of what a reopened thread actually shows — gets the real action
 * rows: copy/edit on a user turn, copy/retry/feedback/speak on an assistant turn. `onRegenerate`/
 * `onEdit` are owned by `ChatClient` (which holds the thread id + the streaming seam), so this
 * component only renders and reports upward; `streaming` disables retry/edit while another turn
 * is already in flight (mirrors the composer's own `streaming` gate on `Send`/`Stop`).
 */
export function PersistedThreadView({
  thread,
  streaming = false,
  onRegenerate,
  onEdit,
}: {
  thread: Thread;
  streaming?: boolean;
  onRegenerate?: (assistantTurnId: string, userText: string) => void;
  onEdit?: (userTurnId: string, newText: string) => void;
}): ReactNode {
  const t = useTranslations("chat.turn");
  const tCaveat = useTranslations("chat.caveat");

  return (
    <div className="flex flex-col gap-6">
      {thread.turns.map((turn) => {
        const isUser = turn.role === "user";
        return (
          <div key={turn.id} className="flex flex-col gap-3">
            {turn.messages.map((message) => {
              if (message.type === "text") {
                const text = typeof message.content.text === "string" ? message.content.text : "";
                return isUser ? (
                  <UserBubble
                    key={message.id}
                    text={text}
                    label={t("you")}
                    onEdit={
                      onEdit && !streaming ? (newText) => onEdit(turn.id, newText) : undefined
                    }
                  />
                ) : (
                  <AnswerMarkdown key={message.id} text={text} />
                );
              }
              if (message.type === "rich_card") {
                const parsed = richCardSchema.safeParse(message.content);
                return parsed.success ? <RichCardView key={message.id} card={parsed.data} /> : null;
              }
              if (message.type === "web_search_sources") {
                // Positioned right below the answer text, above the caveat — mirrors
                // `active-turn-view.tsx`'s live placement of the same component, so the pill
                // doesn't jump position when a turn hands off from the live overlay to this
                // persisted render on reload.
                const parsed = webSearchSourcesBlockSchema.safeParse(message.content);
                return parsed.success ? (
                  <WebSearchSources key={message.id} sources={parsed.data.sources} />
                ) : null;
              }
              // thinking / tool_use / tool_result / steering / branch_summary / compaction blocks
              // are not surfaced in the persisted thread view (process detail is a live-only concern
              // this pass); caveat handled below so it's always present on assistant turns.
              return null;
            })}
            {!isUser ? (
              <>
                <Caveat text={tCaveat("nonTrading")} />
                <AssistantTurnActions
                  turn={turn}
                  onRetry={
                    onRegenerate && !streaming
                      ? (userText) => onRegenerate(turn.id, userText)
                      : undefined
                  }
                />
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** The assistant action row for one PERSISTED turn — owns the feedback vote's local state (seeded
 * `null`; the backend's `TurnFeedbackRequest` has no GET to read back an existing vote, so a vote
 * cast in a prior session isn't reflected until this one is cast again) and resolves `onRetry`'s
 * `userText` argument (the regenerate stream needs the ORIGINAL user text purely for the optimistic
 * bubble the overlay shows while the new answer streams in — the backend re-derives its own copy
 * server-side from the message tree, this is display-only) from the turn's own text block. */
function AssistantTurnActions({
  turn,
  onRetry,
}: {
  turn: Turn;
  onRetry?: (userText: string) => void;
}): ReactNode {
  const [feedback, setFeedback] = useState<FeedbackRating | null>(null);
  const [retrying, setRetrying] = useState(false);

  const textBlock = turn.messages.find((m) => m.type === "text");
  const answerText =
    textBlock && typeof textBlock.content.text === "string" ? textBlock.content.text : "";

  const onFeedback = (rating: FeedbackRating): void => {
    // Toggling the already-active rating still re-submits it (the backend upserts on
    // `(turn_id, user_id)`, no distinct un-vote value in `TurnFeedbackRequest.rating`'s
    // `up|down` pattern) — so a second click on the same button is a harmless no-op resubmission,
    // not a silent no-op on the UI side; the pressed state simply stays as-is.
    setFeedback(rating);
    void submitTurnFeedback(turn.thread_id, turn.id, rating).catch(() => {
      // Best-effort — a failed feedback POST reverts the optimistic toggle rather than leaving
      // the UI claiming a vote that was never recorded server-side.
      setFeedback((current) => (current === rating ? null : current));
    });
  };

  const handleRetry = onRetry
    ? () => {
        setRetrying(true);
        onRetry(answerText);
        // `retrying` only gates this row's own retry button from double-firing while the new
        // stream spins up — `ChatClient` clears the whole overlay on completion, at which point
        // this component unmounts (a fresh persisted turn replaces it), so there's no "stuck
        // spinner" state to reset back to false.
      }
    : undefined;

  return (
    <AssistantActions
      text={answerText}
      onRetry={handleRetry}
      feedback={feedback}
      onFeedback={onFeedback}
      retrying={retrying}
    />
  );
}
