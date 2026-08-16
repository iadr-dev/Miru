"use client";

import {
  BrainIcon,
  CaretDownIcon,
  FileIcon,
  ImageIcon,
  MagnifyingGlassIcon,
} from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { AssistantActions, UserBubble } from "@/app/[locale]/(app)/chat/active-turn-view";
import { AnswerMarkdown } from "@/app/[locale]/(app)/chat/answer-markdown";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { RichCardView } from "@/app/[locale]/(app)/chat/rich-card";
import { WebSearchSources } from "@/app/[locale]/(app)/chat/web-search-sources";
import { type FeedbackRating, submitTurnFeedback } from "@/lib/api/threads";
import { cn } from "@/lib/cn";
import {
  attachmentBlockSchema,
  richCardSchema,
  type Thread,
  type Turn,
  thinkingBlockSchema,
  toolResultBlockSchema,
  toolUseBlockSchema,
  webSearchSourcesBlockSchema,
} from "@/types/conversation";

/** One persisted lane's grouped tool-use/tool-result/thinking blocks, keyed by `agent_id` — the
 * persisted-view counterpart of `turn-store.ts`'s `buildLaneGroups`, built from message blocks
 * instead of SSE frames since a persisted turn has no live lane-status lifecycle (every lane is
 * simply "done" by the time it's read back). */
type PersistedLaneEntry =
  | { kind: "tool"; seq: number; toolName: string; summary: string }
  | { kind: "thinking"; seq: number; text: string };

function groupPersistedLaneEntries(messages: Turn["messages"]): Map<string, PersistedLaneEntry[]> {
  const byAgent = new Map<string, PersistedLaneEntry[]>();
  const pendingToolUse = new Map<string, string>(); // call.id -> tool name, awaiting its result
  const push = (agentId: string, entry: PersistedLaneEntry): void => {
    const existing = byAgent.get(agentId);
    if (existing) {
      existing.push(entry);
    } else {
      byAgent.set(agentId, [entry]);
    }
  };
  for (const message of messages) {
    if (message.type === "tool_use") {
      const parsed = toolUseBlockSchema.safeParse(message.content);
      if (parsed.success) {
        pendingToolUse.set(parsed.data.call.id, parsed.data.activity);
      }
    } else if (message.type === "tool_result") {
      const parsed = toolResultBlockSchema.safeParse(message.content);
      if (parsed.success) {
        const toolName = pendingToolUse.get(parsed.data.result.tool_call_id) ?? "";
        push(parsed.data.agent_id, {
          kind: "tool",
          seq: message.seq,
          toolName,
          summary: parsed.data.result.content,
        });
      }
    } else if (message.type === "thinking") {
      const parsed = thinkingBlockSchema.safeParse(message.content);
      if (parsed.success && parsed.data.text) {
        push(parsed.data.agent_id, { kind: "thinking", seq: message.seq, text: parsed.data.text });
      }
    }
  }
  return byAgent;
}

/** The persisted-view counterpart of `active-turn-view.tsx`'s `ProcessDisclosure` — same
 * 顯示過程/隱藏過程 toggle and per-lane grouped tool/thinking sub-list, built from this turn's
 * durable `tool_use`/`tool_result`/`thinking` blocks instead of live SSE frames. Collapsed by
 * default (unlike the live view's auto-open-while-thinking behavior, which has no equivalent on a
 * turn that already finished) and renders nothing if the turn has no process blocks at all —
 * covers both a `none`/bare `single` turn with no tool calls and any turn persisted before this
 * feature existed. */
function PersistedProcessDisclosure({
  messages,
  t,
}: {
  messages: Turn["messages"];
  t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const [open, setOpen] = useState(false);
  const byAgent = groupPersistedLaneEntries(messages);
  if (byAgent.size === 0) {
    return null;
  }

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "inline-flex min-h-11 items-center gap-1 text-[13px] text-[var(--text-muted)]",
          "transition-colors hover:text-[var(--text)]",
        )}
      >
        <CaretDownIcon
          size={14}
          weight="regular"
          aria-hidden
          className={cn("transition-transform", open && "rotate-180")}
        />
        {open ? t("hideProcess") : t("showProcess")}
      </button>
      {open ? (
        <ol className="mt-2 flex flex-col gap-3" aria-live="off">
          {[...byAgent.entries()].map(([agentId, entries]) => (
            <li key={agentId} className="flex flex-col gap-1.5">
              <ol className="flex flex-col">
                {entries.map((entry, index) => (
                  <PersistedLaneEntryRow
                    key={`${entry.kind}-${entry.seq}`}
                    entry={entry}
                    isLast={index === entries.length - 1}
                    t={t}
                  />
                ))}
              </ol>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

/** One tool/thinking entry inside a persisted lane's sub-list — mirrors
 * `active-turn-view.tsx`'s `LaneSubEntryRow` marker/layout so a turn's process trace looks the
 * same whether read live or from a reload. */
function PersistedLaneEntryRow({
  entry,
  isLast,
  t,
}: {
  entry: PersistedLaneEntry;
  isLast: boolean;
  t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <li className="relative flex gap-3 pb-3 last:pb-0">
      <div className="relative flex w-3 shrink-0 justify-center">
        {isLast ? null : (
          <span className="absolute top-2 bottom-0 w-px bg-[var(--border)]" aria-hidden />
        )}
        <span className="relative z-10 mt-1 flex size-3.5 shrink-0 items-center justify-center bg-[var(--bg)]">
          {entry.kind === "tool" ? (
            <MagnifyingGlassIcon
              size={13}
              weight="regular"
              aria-hidden
              className="text-[var(--text-muted)]"
            />
          ) : (
            <BrainIcon
              size={13}
              weight="regular"
              aria-hidden
              className="text-[var(--text-muted)]"
            />
          )}
        </span>
      </div>
      <div className="min-w-0 flex-1 pt-0.5 text-[13px] text-[var(--text-muted)]">
        {entry.kind === "tool" ? (
          <span>{t("toolStep", { tool: entry.toolName })}</span>
        ) : (
          <div className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide">
              {t("thinkingChunk")}
            </span>
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-[var(--text)]">
              {entry.text}
            </p>
          </div>
        )}
      </div>
    </li>
  );
}

/** A persisted `attachment` block's filename chip — read-only (no remove control, unlike the
 * composer's staging `AttachmentPreview`; a sent turn's attachments are historical record, not
 * editable). Renders only the filename + type icon, never the raw bytes (the backend never
 * persists them past the turn — see `AttachmentBlock`'s own docstring), and never the extracted
 * document text inline (the model already saw it folded into the turn's prompt; re-rendering it
 * here would duplicate potentially-long content the user never asked to see again). */
function AttachmentChip({ filename, isImage }: { filename: string; isImage: boolean }): ReactNode {
  return (
    <span
      className={cn(
        "inline-flex max-w-48 items-center gap-1.5 rounded-[var(--radius-card)] border border-[var(--border)]",
        "bg-[var(--surface-2)] px-2.5 py-1.5 text-[13px] text-[var(--text)]",
      )}
    >
      {isImage ? (
        <ImageIcon size={14} weight="regular" className="shrink-0 text-[var(--text-muted)]" />
      ) : (
        <FileIcon size={14} weight="regular" className="shrink-0 text-[var(--text-muted)]" />
      )}
      <span className="min-w-0 flex-1 truncate">{filename}</span>
    </span>
  );
}

/** One turn's message blocks rendered per `type` — split out of `PersistedThreadView` purely to
 * keep that component's own branching shallow. `thinking`/`tool_use`/`tool_result` blocks are
 * rendered separately by `PersistedProcessDisclosure` (grouped by lane, not inline per-block,
 * since they need the whole turn's messages to pair a `tool_use` with its `tool_result` and group
 * by `agent_id` — see that component). `steering`/`branch_summary`/`compaction` blocks still have
 * no persisted-view renderer; the caveat is handled by the caller so it's always present on
 * assistant turns. */
function renderMessageBlock(
  message: Turn["messages"][number],
  opts: {
    isUser: boolean;
    youLabel: string;
    onEditText?: (newText: string) => void;
  },
): ReactNode {
  const { isUser, youLabel, onEditText } = opts;
  if (
    message.type === "tool_use" ||
    message.type === "tool_result" ||
    message.type === "thinking"
  ) {
    return null; // handled by `PersistedProcessDisclosure`, rendered once per turn below
  }
  if (message.type === "text") {
    const text = typeof message.content.text === "string" ? message.content.text : "";
    return isUser ? (
      <UserBubble key={message.id} text={text} label={youLabel} onEdit={onEditText} />
    ) : (
      <AnswerMarkdown key={message.id} text={text} />
    );
  }
  if (message.type === "attachment") {
    const parsed = attachmentBlockSchema.safeParse(message.content);
    if (!parsed.success) {
      return null;
    }
    // Attachment chips sit right-aligned above the user bubble they belong to, mirroring the
    // composer's own staged-attachment row position (above the input, not inline in the text).
    return (
      <div key={message.id} className="flex justify-end">
        <AttachmentChip filename={parsed.data.filename} isImage={parsed.data.is_image} />
      </div>
    );
  }
  if (message.type === "rich_card") {
    const parsed = richCardSchema.safeParse(message.content);
    return parsed.success ? <RichCardView key={message.id} card={parsed.data} /> : null;
  }
  if (message.type === "web_search_sources") {
    // Positioned right below the answer text, above the caveat — mirrors
    // `active-turn-view.tsx`'s live placement of the same component, so the pill doesn't jump
    // position when a turn hands off from the live overlay to this persisted render on reload.
    const parsed = webSearchSourcesBlockSchema.safeParse(message.content);
    return parsed.success ? (
      <WebSearchSources key={message.id} sources={parsed.data.sources} />
    ) : null;
  }
  return null;
}

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
            {turn.messages.map((message) =>
              renderMessageBlock(message, {
                isUser,
                youLabel: t("you"),
                onEditText:
                  onEdit && !streaming ? (newText) => onEdit(turn.id, newText) : undefined,
              }),
            )}
            {!isUser ? <PersistedProcessDisclosure messages={turn.messages} t={t} /> : null}
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
