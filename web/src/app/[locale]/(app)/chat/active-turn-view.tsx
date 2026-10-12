"use client";

import {
  ArrowsClockwiseIcon,
  BrainIcon,
  CaretDownIcon,
  CheckIcon,
  CopyIcon,
  MagnifyingGlassIcon,
  PencilSimpleIcon,
  SpeakerHighIcon,
  StopIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { AnswerMarkdown } from "@/app/[locale]/(app)/chat/answer-markdown";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { MemoryUsedChip } from "@/app/[locale]/(app)/chat/memory-used-chip";
import { PersonalityChip } from "@/app/[locale]/(app)/chat/personality-chip";
import { RichCardView } from "@/app/[locale]/(app)/chat/rich-card";
import { TokenUsageChip } from "@/app/[locale]/(app)/chat/token-usage-chip";
import {
  type ActiveTurn,
  buildLaneGroups,
  type LaneGroup,
} from "@/app/[locale]/(app)/chat/turn-store";
import { WebSearchSources } from "@/app/[locale]/(app)/chat/web-search-sources";
import { Link } from "@/i18n/navigation";
import type { FeedbackRating } from "@/lib/api/threads";
import { audioBase64ToObjectUrl, speakText } from "@/lib/api/voice";
import { cn } from "@/lib/cn";
import { markdownToSpeech } from "@/lib/markdown-to-speech";
import { useTypewriter } from "@/lib/use-typewriter";

/**
 * Renders the in-flight (or just-finished-but-not-yet-persisted) turn from the Zustand overlay.
 * The user bubble shows immediately, then a "thinking" state driven by `agent.spawned`/
 * `agent.progress` + a disclosure (auto-expands once the turn leaves "thinking") fed by
 * `tool.result` summaries and genuinely-streamed `thinking.delta` reasoning chunks. The final
 * answer now genuinely token-streams too for `none`/`single`-framework turns
 * (`turn.streamingAnswerText`, from `message.delta`) — rendered live, `aria-live="off"`, while
 * the terminal `message` frame hasn't landed. Because `message.delta` batches per completed,
 * guardrail-scanned sentence rather than per raw token (backend sentence-delayed streaming), the
 * raw stream still arrives in sentence-sized jumps; `useTypewriter` smooths that into a steady
 * character-by-character reveal (the Claude-Code-style typing effect) before it's handed to
 * `AnswerMarkdown`, catching up only if a backlog piles up, and snapping to full text instantly
 * under `prefers-reduced-motion`. Once the terminal frame lands, `turn.answerText` takes over as
 * the `aria-live="polite"` final render (the only element with `polite` — thinking/tool/reasoning/
 * streaming-partial narration all stay `off` per web/OVERVIEW.md §7). A `subagent`/`team` turn
 * never populates `streamingAnswerText` (see `turn-store.ts`'s module docstring), so those still
 * render exactly as before: nothing, then the complete answer in one shot.
 */
export function ActiveTurnView({
  turn,
  onAskUserPick,
}: {
  turn: ActiveTurn;
  onAskUserPick: (prompt: string) => void;
}): ReactNode {
  const t = useTranslations("chat.turn");
  const tCaveat = useTranslations("chat.caveat");
  const reduceMotion = useReducedMotion();
  const typedStreamingAnswer = useTypewriter(turn.streamingAnswerText ?? "", reduceMotion ?? false);

  const thinking =
    turn.phase === "thinking" && turn.answerText === null && turn.streamingAnswerText === null;
  const showProcess =
    turn.toolSteps.length > 0 || turn.lanes.length > 0 || turn.thinkingChunks.length > 0;

  return (
    <div className="flex flex-col gap-4">
      <UserBubble text={turn.userText} label={t("you")} />

      <div className="flex flex-col">
        {thinking ? (
          <p
            className="inline-flex items-center gap-2 text-[var(--text-muted)] text-sm"
            // Thinking narration is explicitly NOT announced (aria-live off) per §7.
            aria-live="off"
          >
            <span className="size-2 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden />
            {t("thinking")}
          </p>
        ) : null}

        {showProcess ? <ProcessDisclosure turn={turn} /> : null}

        {turn.steeringQueued.length > 0 ? (
          <p className="mt-2 text-[13px] text-[var(--text-muted)]">{t("steeringQueued")}</p>
        ) : null}

        {turn.richCards.map((entry) => (
          <RichCardView key={entry.seq} card={entry.card} onAskUserPick={onAskUserPick} />
        ))}

        {turn.answerText !== null ? (
          <div className="mt-1" aria-live="polite">
            <AnswerMarkdown text={turn.answerText} />
            <WebSearchSources sources={turn.webSearchSources} />
            <Caveat text={tCaveat("nonTrading")} />
            <MemoryUsedChip memories={turn.memoriesUsed} />
            <PersonalityChip presetId={turn.personality} />
            <TokenUsageChip usage={turn.usage} />
            {/* No retry/feedback target here — this overlay is about to be replaced by the
             * persisted-history render once the thread query settles (see `ChatClient`'s
             * `runStream`), which is where those actions are wired for real (needs a real,
             * persisted turn id). Copy/read-aloud need no turn id, so they're available now. */}
            <AssistantActions text={turn.answerText} />
          </div>
        ) : turn.streamingAnswerText !== null ? (
          // The live partial render while the terminal `message` frame hasn't landed yet —
          // deliberately `aria-live="off"` (matches the thinking/tool narration rule in §7):
          // `aria-live="polite"` above fires only once, on the FINAL answer, not on every
          // growing partial. No actions/caveat/sources here either — those are only
          // meaningful once the complete, governance-finalized text exists.
          <div className="mt-1" aria-live="off">
            <AnswerMarkdown text={typedStreamingAnswer} />
          </div>
        ) : null}

        {turn.phase === "error" ? (
          <InlineError message={turn.errorMessage} fallback={t("error")} code={turn.errorCode} />
        ) : null}
        {turn.phase === "cancelled" ? (
          <p className="mt-2 text-[var(--text-muted)] text-sm">{t("cancelled")}</p>
        ) : null}
      </div>
    </div>
  );
}

/**
 * `text` renders as the bubble body by default. `onEdit` (persisted, thread-scoped user turns
 * only — a live in-flight bubble has no turn id to edit yet) swaps the bubble for an inline
 * textarea when the pencil action is pressed; `onSave`/`onCancel` are the edit form's own
 * controls. Action row hover convention (opacity-0 → group-hover/focus-visible) mirrors
 * `recents-list.tsx`'s `ThreadRow` "more options" trigger — the established hover-reveal pattern
 * in this codebase, reused here rather than invented fresh.
 */
export function UserBubble({
  text,
  label,
  onEdit,
  onResend,
}: {
  text: string;
  label: string;
  onEdit?: (newText: string) => void;
  onResend?: () => void;
}): ReactNode {
  const t = useTranslations("chat.turn.actions");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);

  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="flex w-full max-w-[85%] flex-col gap-2">
          <textarea
            // biome-ignore lint/a11y/noAutofocus: user just pressed "Edit message" — this textarea replacing the bubble's static text is the intended focus target.
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            className={cn(
              "w-full resize-none rounded-[var(--radius-card)] border border-[var(--accent)]",
              "bg-[var(--surface)] px-4 py-2.5 text-[15px] text-[var(--text)] leading-relaxed",
              "!outline-none",
            )}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setDraft(text);
                setEditing(false);
              }}
              aria-label={t("editCancel")}
              className={cn(
                "inline-flex min-h-9 items-center rounded-[var(--radius-button)] px-3 text-[13px]",
                "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)]",
              )}
            >
              {t("editCancel")}
            </button>
            <button
              type="button"
              onClick={() => {
                const trimmed = draft.trim();
                setEditing(false);
                if (trimmed.length > 0 && trimmed !== text) {
                  onEdit?.(trimmed);
                }
              }}
              aria-label={t("editSave")}
              className={cn(
                "inline-flex min-h-9 items-center rounded-[var(--radius-button)] bg-[var(--accent)] px-3",
                "text-[13px] text-[var(--bg)] transition-opacity hover:opacity-90",
              )}
            >
              {t("editSave")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex flex-col items-end gap-1">
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-wrap rounded-[var(--radius-card)] bg-[var(--surface-2)]",
          "px-4 py-2.5 text-[15px] text-[var(--text)] leading-relaxed",
        )}
      >
        <span className="sr-only">{label}: </span>
        {text}
      </div>
      <div
        className={cn(
          "flex items-center gap-0.5 opacity-0 transition-opacity",
          "focus-within:opacity-100 group-hover:opacity-100",
        )}
      >
        <CopyButton text={text} />
        {onEdit ? (
          <ActionIconButton
            icon={PencilSimpleIcon}
            label={t("edit")}
            onClick={() => {
              setDraft(text);
              setEditing(true);
            }}
          />
        ) : null}
        {onResend ? (
          <ActionIconButton icon={ArrowsClockwiseIcon} label={t("resend")} onClick={onResend} />
        ) : null}
      </div>
    </div>
  );
}

/** The assistant-answer action row: copy, retry/regenerate, thumbs up/down, read-aloud. Shared by
 * the live overlay (`ActiveTurnView`, no feedback/retry/speak target yet — no persisted turn id
 * during streaming) and `PersistedThreadView` (every action wired for real). Any handler left
 * `undefined` simply omits that button rather than rendering a disabled stub, since a live
 * in-flight answer has no turn id for feedback/retry/speak to act on until it's persisted. */
export function AssistantActions({
  text,
  onRetry,
  feedback,
  onFeedback,
  retrying,
}: {
  text: string;
  onRetry?: () => void;
  feedback?: FeedbackRating | null;
  onFeedback?: (rating: FeedbackRating) => void;
  retrying?: boolean;
}): ReactNode {
  const t = useTranslations("chat.turn.actions");

  return (
    <div className="mt-2 flex items-center gap-0.5">
      <CopyButton text={text} />
      {onRetry ? (
        <ActionIconButton
          icon={ArrowsClockwiseIcon}
          label={t("retry")}
          onClick={onRetry}
          disabled={retrying}
          spin={retrying}
        />
      ) : null}
      {onFeedback ? (
        <>
          <ActionIconButton
            icon={ThumbsUpIcon}
            label={t("thumbsUp")}
            weight={feedback === "up" ? "fill" : "regular"}
            active={feedback === "up"}
            onClick={() => onFeedback("up")}
          />
          <ActionIconButton
            icon={ThumbsDownIcon}
            label={t("thumbsDown")}
            weight={feedback === "down" ? "fill" : "regular"}
            active={feedback === "down"}
            onClick={() => onFeedback("down")}
          />
        </>
      ) : null}
      <SpeakButton text={text} />
    </div>
  );
}

/** Copy-to-clipboard icon button with the same brief checkmark-swap micro-interaction as
 * `composer.tsx`'s `ScreenshotPreview`/mic-state-morph icon swaps (gated through
 * `useReducedMotion()`, same convention). */
function CopyButton({ text }: { text: string }): ReactNode {
  const t = useTranslations("chat.turn.actions");
  const [copied, setCopied] = useState(false);
  const reduceMotion = useReducedMotion();
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied/unavailable — no toast, matching this codebase's convention
      // for non-fatal UI dismissals (see composer.tsx's screenshot-capture cancel).
    }
  }, [text]);

  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copied ? t("copied") : t("copy")}
      title={copied ? t("copied") : t("copy")}
      className={cn(
        "relative inline-flex size-8 items-center justify-center rounded-[var(--radius-button)]",
        "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
      )}
    >
      <AnimatePresence initial={false} mode="wait">
        <motion.span
          key={copied ? "copied" : "copy"}
          initial={{ opacity: 0, scale: 0.6 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.6 }}
          transition={{ duration: reduceMotion ? 0 : 0.15 }}
          className="inline-flex"
        >
          {copied ? (
            <CheckIcon size={15} weight="regular" className="text-[var(--accent)]" />
          ) : (
            <CopyIcon size={15} weight="regular" />
          )}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}

/** Read-aloud icon button: `POST /voice/speak` → base64 audio → `<audio>` playback. Morphs
 * speaker → stop while playing, the same idle/active icon-swap language `composer.tsx`'s
 * `VoiceOrSendButton` uses for its mic state machine. TTS is Free/unrestricted, so a failure here
 * is a genuine network/provider outcome, not an entitlement wall — it still surfaces as a brief
 * inline message rather than a silent no-op, since unlike a dismissed permission prompt this is
 * something the user should understand happened. */
function SpeakButton({ text }: { text: string }): ReactNode {
  const t = useTranslations("chat.turn.actions");
  const locale = useLocale();
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [error, setError] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const onToggle = useCallback(async () => {
    if (state === "playing") {
      cleanup();
      setState("idle");
      return;
    }
    setError(false);
    setState("loading");
    try {
      const response = await speakText(markdownToSpeech(text), locale);
      const url = audioBase64ToObjectUrl(response.audio_base64, response.content_type);
      objectUrlRef.current = url;
      const audio = new Audio(url);
      audio.onended = () => {
        cleanup();
        setState("idle");
      };
      audioRef.current = audio;
      await audio.play();
      setState("playing");
    } catch {
      cleanup();
      setState("idle");
      setError(true);
    }
  }, [state, text, locale, cleanup]);

  return (
    <div className="inline-flex items-center gap-1.5">
      <ActionIconButton
        icon={state === "playing" ? StopIcon : SpeakerHighIcon}
        weight={state === "playing" ? "fill" : "regular"}
        label={state === "playing" ? t("stopSpeaking") : t("speak")}
        onClick={onToggle}
        disabled={state === "loading"}
        spin={false}
      />
      {error ? <span className="text-[12px] text-[var(--warn)]">{t("speakError")}</span> : null}
    </div>
  );
}

/** One small icon button shared by every action row above — same 32px hit target + hover/focus
 * treatment throughout, `weight`/`active` let a caller show a filled/accent state (thumbs
 * up/down). */
function ActionIconButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  weight = "regular",
  active = false,
  spin = false,
}: {
  icon: typeof PencilSimpleIcon;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  weight?: "regular" | "fill";
  active?: boolean;
  spin?: boolean;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      aria-pressed={active}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-[var(--radius-button)]",
        "transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        active ? "text-[var(--accent)]" : "text-[var(--text-muted)]",
      )}
    >
      <Icon size={15} weight={weight} className={spin ? "animate-spin" : undefined} />
    </button>
  );
}

/**
 * The 顯示過程/隱藏過程 disclosure: lane statuses, redacted `tool.result` summaries, and
 * accumulated `thinking.delta` reasoning chunks — grouped by owning lane (`buildLaneGroups`), each
 * lane rendering its own nested sub-list of tool/thinking entries, rather than one flat timeline
 * interleaving every lane's activity together. This matters most for `subagent` (independent
 * parallel lanes) and `team` (sequential lanes, where lane 2 doesn't even start until lane 1's
 * `Finding` exists) — a flat merge made both look identical and hid the sequential dependency.
 * Visual pattern reused from `chat/cards/event-timeline.tsx` (the calendar/corporate-action
 * timeline): a continuous vertical rule with a small dot marker per entry, newest-at-bottom instead
 * of that component's newest-first, since this timeline narrates a turn's chronology rather than
 * dated history — now applied once per lane group instead of once globally.
 *
 * Auto-follow: opens itself the moment the turn STARTS thinking, then auto-closes itself the
 * moment it stops (done/error/cancelled) — driven only on the ACTUAL phase transition
 * (`prevPhaseRef`), never re-firing on an unrelated re-render while phase is unchanged. The moment
 * the user manually clicks the toggle, `userControlledRef` latches for the rest of this turn: every
 * later phase transition is still tracked (so a NEW turn reusing this component starts fresh — see
 * the mount-per-turn note below) but no longer moves `open` out from under the user's own choice.
 */
function ProcessDisclosure({ turn }: { turn: ActiveTurn }): ReactNode {
  const t = useTranslations("chat.turn");
  const [open, setOpen] = useState(turn.phase === "thinking");
  const prevPhaseRef = useRef(turn.phase);
  const userControlledRef = useRef(false);

  useEffect(() => {
    const prevPhase = prevPhaseRef.current;
    prevPhaseRef.current = turn.phase;
    if (prevPhase === turn.phase || userControlledRef.current) {
      return;
    }
    if (turn.phase === "thinking") {
      setOpen(true);
    } else if (prevPhase === "thinking") {
      setOpen(false);
    }
  }, [turn.phase]);

  const groups = buildLaneGroups(turn);

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => {
          userControlledRef.current = true;
          setOpen((v) => !v);
        }}
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
        // Thinking/tool narration stays aria-live="off" throughout — only the final answer
        // container (below, in the parent) is "polite" (web/OVERVIEW.md §7).
        <ol className="mt-2 flex flex-col gap-3" aria-live="off">
          {groups.map((group) => (
            <LaneGroupRow key={group.agentId ?? "root"} group={group} t={t} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}

/** One lane's header row (skipped for the root/no-lane bucket) plus its nested sub-timeline of
 * tool/thinking entries — the `|_` indentation from the flat-timeline predecessor's visual rule,
 * scoped to just this lane's own entries instead of spanning the whole turn. */
function LaneGroupRow({
  group,
  t,
}: {
  group: LaneGroup;
  t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const degraded = group.lane?.status === "degraded";
  const dotClass = degraded
    ? "bg-[var(--warn)]"
    : group.lane?.status === "running"
      ? "bg-[var(--accent)]"
      : "bg-[var(--text-muted)]";

  return (
    <li className="flex flex-col gap-1.5">
      {group.lane ? (
        <div className="flex items-center gap-2 text-[13px]">
          <span className={cn("size-2 shrink-0 rounded-full", dotClass)} aria-hidden />
          {degraded ? (
            <span className="inline-flex items-center gap-1 text-[var(--warn)]">
              <WarningIcon size={12} weight="regular" aria-hidden />
              {t("laneDegraded")}
            </span>
          ) : (
            <span className="text-[var(--text)]">
              {group.lane.activity ?? group.lane.skill ?? group.lane.agentId}
            </span>
          )}
        </div>
      ) : null}
      {group.entries.length > 0 ? (
        <ol className={cn("flex flex-col", group.lane ? "ml-4" : undefined)}>
          {group.entries.map((entry, index) => (
            <ProcessSubEntryRow
              key={`${entry.kind}-${entry.seq}`}
              entry={
                entry.kind === "tool"
                  ? {
                      kind: "tool",
                      toolName: entry.step.toolName,
                      running: entry.step.status === "running",
                    }
                  : { kind: "thinking", text: entry.chunk.text }
              }
              isLast={index === group.entries.length - 1}
              t={t}
            />
          ))}
        </ol>
      ) : null}
    </li>
  );
}

/** Normalized shape both the live (`LaneGroup["entries"]`) and persisted
 * (`PersistedLaneEntry`) sub-entry unions reduce to before reaching `ProcessSubEntryRow` — one
 * render body shared by the live and reload paths instead of two near-identical copies.
 * `running` is only ever `true` on the live path (a `tool.start`-only placeholder still awaiting
 * its `tool.result`) — a persisted turn has no such state, every block it carries already
 * finished, so `persisted-thread-view.tsx` never passes it. */
export type ProcessSubEntry =
  | { kind: "tool"; toolName: string; running?: boolean }
  | { kind: "thinking"; text: string };

/** One tool/thinking entry inside a lane's own sub-timeline (live or persisted) — the lane-dot
 * marker lives in the caller's own group header, so this only ever renders the tool-icon/
 * brain-icon markers. Exported for `persisted-thread-view.tsx`'s reload-path reuse. */
export function ProcessSubEntryRow({
  entry,
  isLast,
  t,
}: {
  entry: ProcessSubEntry;
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
              className={cn(
                "text-[var(--text-muted)]",
                entry.running && "animate-pulse text-[var(--accent)]",
              )}
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
          <span>{t(entry.running ? "toolStepRunning" : "toolStep", { tool: entry.toolName })}</span>
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

/** Refusals that are solved by a plan or a key, so the error offers the way out. */
const UPGRADE_CODES = new Set([
  "chat_limit_reached",
  "deep_research_limit_reached",
  "feature_not_entitled",
]);

function InlineError({
  message,
  fallback,
  code,
}: {
  message: string | null;
  fallback: string;
  code?: string | null;
}): ReactNode {
  const tUpgrade = useTranslations("chat.turn");
  return (
    <div
      role="alert"
      className={cn(
        "mt-2 inline-flex items-start gap-2 rounded-[var(--radius-input)] border px-3 py-2",
        "border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[var(--surface-2)] text-sm",
      )}
    >
      <WarningIcon size={16} weight="regular" aria-hidden className="mt-0.5 text-[var(--warn)]" />
      <span className="text-[var(--text)]">
        {message && message.length > 0 ? message : fallback}
        {code && UPGRADE_CODES.has(code) ? (
          <>
            {" "}
            <Link
              href="/settings/billing"
              className="text-[var(--accent-text)] underline-offset-2 hover:underline"
            >
              {tUpgrade("seePlans")}
            </Link>
          </>
        ) : null}
      </span>
    </div>
  );
}
