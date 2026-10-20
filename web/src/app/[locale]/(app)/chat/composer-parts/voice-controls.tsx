"use client";

import {
  ArrowUpIcon,
  CheckIcon,
  CircleNotchIcon,
  MicrophoneIcon,
  WaveformIcon,
  XIcon,
} from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";

/** Shared entrance/exit animation for every `VoiceOrSendButton` state — split out so each
 * per-state button below only spells out what's actually different about it. */
function useButtonMotionProps(reduceMotion: boolean | null): {
  initial: { opacity: number; scale: number };
  animate: { opacity: number; scale: number };
  exit: { opacity: number; scale: number };
  transition: { duration: number };
} {
  return {
    initial: { opacity: 0, scale: 0.6 },
    animate: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.6 },
    transition: { duration: reduceMotion ? 0 : 0.15 },
  };
}

function SendButton({
  sendLabel,
  reduceMotion,
}: Readonly<{
  sendLabel: string;
  reduceMotion: boolean | null;
}>): ReactNode {
  return (
    <motion.button
      key="send"
      type="submit"
      aria-label={sendLabel}
      title={sendLabel}
      {...useButtonMotionProps(reduceMotion)}
      className={cn(
        "absolute inset-0 inline-flex items-center justify-center rounded-[var(--radius-button)]",
        "bg-[var(--accent)] text-[var(--bg)] transition-opacity hover:opacity-90 active:scale-95",
      )}
    >
      <ArrowUpIcon size={20} weight="bold" />
    </motion.button>
  );
}

/** The X/✓ pair shown in place of the send button while `voiceState === "recording"` — matches
 * the Claude Code composer's dictation confirm/cancel pattern (see chat/README.md's voice-input
 * spec): ✕ discards the capture outright, ✓ stops and transcribes+sends (the default
 * `finishRecording` path). Both live where the send button normally sits, not in the feature
 * menu, since they're the two only-currently-valid actions while recording — the textarea itself
 * is disabled during this state (see the `disabled` prop below its own render). */
export function RecordingControls({
  cancelLabel,
  confirmLabel,
  onCancel,
  onConfirm,
}: Readonly<{
  cancelLabel: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}>): ReactNode {
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <button
        type="button"
        onClick={onCancel}
        aria-label={cancelLabel}
        title={cancelLabel}
        className={cn(
          "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
          "bg-[var(--surface-2)] text-[var(--text-muted)] transition-colors hover:bg-[var(--border)] hover:text-[var(--text)]",
          "active:scale-95",
        )}
      >
        <XIcon size={18} weight="bold" />
      </button>
      <button
        type="button"
        onClick={onConfirm}
        aria-label={confirmLabel}
        title={confirmLabel}
        className={cn(
          "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
          "bg-[var(--accent)] text-[var(--bg)] transition-opacity hover:opacity-90 active:scale-95",
        )}
      >
        <CheckIcon size={18} weight="bold" />
      </button>
    </div>
  );
}

/** The always-visible trigger into the fully separate continuous voice-conversation overlay
 * (`VoiceModeOverlay`) — distinct from the dictation mic (`MicrophoneIcon`/`StartRecordingButton`
 * above): this one never records anything itself, it only opens the hands-free surface. Only
 * shown when the draft is empty and no dictation is in progress (mirrors when the dictation mic
 * itself is visible) — once there's text to send, the composer's job is sending it, not offering
 * a second voice affordance. */
export function VoiceModeTriggerButton({
  label,
  onClick,
}: Readonly<{
  label: string;
  onClick: () => void;
}>): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
        "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      <WaveformIcon size={20} weight="regular" />
    </button>
  );
}

function TranscribingButton({
  voiceLabel,
  reduceMotion,
}: Readonly<{
  voiceLabel: string;
  reduceMotion: boolean | null;
}>): ReactNode {
  return (
    <motion.button
      key="transcribing"
      type="button"
      disabled
      aria-label={voiceLabel}
      title={voiceLabel}
      {...useButtonMotionProps(reduceMotion)}
      className={cn(
        "absolute inset-0 inline-flex items-center justify-center rounded-[var(--radius-button)]",
        "text-[var(--text-muted)]",
      )}
    >
      <CircleNotchIcon
        size={20}
        weight="regular"
        className={reduceMotion ? undefined : "animate-spin"}
      />
    </motion.button>
  );
}

function StartRecordingButton({
  voiceLabel,
  streaming,
  reduceMotion,
  onStartRecording,
}: Readonly<{
  voiceLabel: string;
  streaming: boolean;
  reduceMotion: boolean | null;
  onStartRecording: () => void;
}>): ReactNode {
  const [hovering, setHovering] = useState(false);
  return (
    <motion.button
      key="voice"
      type="button"
      disabled={streaming}
      onClick={onStartRecording}
      aria-label={voiceLabel}
      title={voiceLabel}
      onHoverStart={() => setHovering(true)}
      onHoverEnd={() => setHovering(false)}
      {...useButtonMotionProps(reduceMotion)}
      className={cn(
        "absolute inset-0 inline-flex items-center justify-center rounded-[var(--radius-button)]",
        "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        streaming && "opacity-50",
      )}
    >
      <motion.span
        className="inline-flex"
        animate={
          hovering && !reduceMotion
            ? { scale: [1, 1.15, 0.95, 1.08, 1], opacity: [1, 0.75, 1, 0.85, 1] }
            : { scale: 1, opacity: 1 }
        }
        transition={{ duration: 0.6, ease: "easeInOut" }}
      >
        <MicrophoneIcon size={20} weight="regular" />
      </motion.span>
    </motion.button>
  );
}

/** The trailing composer control for the non-recording states — `RecordingControls` (X/✓) takes
 * over entirely while `voiceState === "recording"` (rendered by the caller instead of this).
 * - `canSend` (any non-whitespace draft): the real send button (➤).
 * - idle + empty draft: the dictation mic — clicking it starts a real `getUserMedia` recording.
 * - `voiceState === "transcribing"`: a brief disabled spinner while `/voice/transcribe` is in
 *   flight — the mic is already released by then (see the caller's `recorder.onstop`). */
export function VoiceOrSendButton({
  canSend,
  voiceState,
  streaming,
  onStartRecording,
  voiceLabel,
  sendLabel,
}: Readonly<{
  canSend: boolean;
  voiceState: "idle" | "recording" | "transcribing";
  streaming: boolean;
  onStartRecording: () => void;
  voiceLabel: string;
  sendLabel: string;
}>): ReactNode {
  const reduceMotion = useReducedMotion();

  return (
    <div className="relative size-11 shrink-0">
      <AnimatePresence initial={false} mode="wait">
        {canSend ? (
          <SendButton sendLabel={sendLabel} reduceMotion={reduceMotion} />
        ) : voiceState === "transcribing" ? (
          <TranscribingButton voiceLabel={voiceLabel} reduceMotion={reduceMotion} />
        ) : (
          <StartRecordingButton
            voiceLabel={voiceLabel}
            streaming={streaming}
            reduceMotion={reduceMotion}
            onStartRecording={onStartRecording}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
