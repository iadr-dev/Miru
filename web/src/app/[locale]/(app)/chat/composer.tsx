"use client";

import { SquareIcon, WarningIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import {
  type ClipboardEvent,
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { AttachmentPreview } from "@/app/[locale]/(app)/chat/composer-parts/attachments";
import { FeatureMenu } from "@/app/[locale]/(app)/chat/composer-parts/feature-menu";
import { ModelMenu } from "@/app/[locale]/(app)/chat/composer-parts/model-menu";
import { captureScreenshot } from "@/app/[locale]/(app)/chat/composer-parts/screenshot";
import { ToneMenu } from "@/app/[locale]/(app)/chat/composer-parts/tone-menu";
import {
  RecordingControls,
  VoiceModeTriggerButton,
  VoiceOrSendButton,
} from "@/app/[locale]/(app)/chat/composer-parts/voice-controls";
import type { AttachmentInput } from "@/lib/api/agent-stream";
import {
  ACCEPTED_ATTACHMENT_MEDIA_TYPES,
  fileToPendingAttachment,
  MAX_ATTACHMENTS_PER_TURN,
  type PendingAttachment,
  pastedTextToPendingAttachment,
  screenshotToPendingAttachment,
  shouldAttachPastedText,
} from "@/lib/api/attachments";
import { transcribeAudio } from "@/lib/api/voice";
import { autogrowTextarea } from "@/lib/autogrow";
import { cn } from "@/lib/cn";

/**
 * The chat composer (chat/README.md §Composer). Auto-grow textarea; the send button MORPHS between
 * idle→send (➤) and streaming→stop (■). The disclaimer line under it reuses the exact governance
 * copy already in the catalog (`disclaimer.text`) — not rewritten.
 *
 * "Add files or photos" and "Take a screenshot" are both wired for real: the backend already has
 * a full attachment pipeline (`api/attachments.py::process_attachments` — image → vision input,
 * document → `markitdown` text, up to `MAX_ATTACHMENTS_PER_TURN` per turn) that `/agent/query`
 * and the `edit` turn action both accept via their `attachments` field; this component's job is
 * only to stage `PendingAttachment[]` (picked files + captured screenshots share one
 * representation, see `lib/api/attachments.ts`) and hand the wire `AttachmentInput[]` up to
 * `onSend`/the caller's edit path on submit — it never calls the API itself.
 *
 * The model-selector IS wired for real: `modelOverride`/`onModelOverrideChange` are controlled by
 * the caller (`ChatClient`, which owns the current thread's id/version and the `patchThread` call
 * — see its own docstring for the pending-override handshake a not-yet-created thread needs).
 * This component only renders the picker and reports a selection upward; it never calls the API
 * itself.
 *
 * "Web search" is likewise wired for real now that `/agent/query` (and the `regenerate`/`edit`
 * turn actions) accept a `web_search: bool` field — `webSearchEnabled`/`onWebSearchEnabledChange`
 * are controlled by the caller the same way, and it defaults to enabled on the caller's side.
 *
 * Two distinct voice affordances, per chat/README.md's voice-input spec — never conflate them:
 * - **Dictation mic** (`MicrophoneIcon`): one-shot speech-to-text INTO this composer. Tap → listen
 *   → tap ✓ to confirm (transcribes then immediately sends, per product decision — no separate
 *   review step) or ✕ to cancel (discards, never calls `/voice/transcribe`).
 * - **Continuous voice mode** (`WaveformIcon`, `onOpenVoiceMode`): opens a fully separate
 *   hands-free conversation overlay (`VoiceModeOverlay`) — this component only renders the
 *   trigger; the overlay owns its own listen→transcribe→send→speak→relisten cycle.
 */
export function Composer({
  streaming,
  onSend,
  onStop,
  modelOverride,
  onModelOverrideChange,
  toneOverride,
  onToneOverrideChange,
  webSearchEnabled,
  onWebSearchEnabledChange,
  deepResearch,
  deepResearchLocked,
  byokActive,
  onOpenVoiceMode,
}: Readonly<{
  streaming: boolean;
  onSend: (text: string, attachments: AttachmentInput[]) => void;
  onStop: () => void;
  modelOverride: string | null;
  /** Null clears the chat's own pick, so it follows the caller's default model again. */
  onModelOverrideChange: (modelId: string | null) => void;
  /** This chat's own 語氣 preset id, or null to follow the account setting. */
  toneOverride: string | null;
  /** A preset id, or `FOLLOW_ACCOUNT_TONE` to clear the chat's own pick. */
  onToneOverrideChange: (presetId: string) => void;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  /** Null when the caller's plan has no deep research (the menu row is then hidden). */
  deepResearch: { enabled: boolean; onChange: (enabled: boolean) => void } | null;
  /** True when the caller's plan has no deep research: the row is shown locked, with the way to
   * upgrade, rather than hidden. */
  deepResearchLocked: boolean;
  /** Whether the caller runs on their own keys. Only then is the model picker shown. */
  byokActive: boolean | undefined;
  onOpenVoiceMode: () => void;
}>): ReactNode {
  const t = useTranslations("chat.composer");
  const tDisc = useTranslations("disclaimer");
  const locale = useLocale();
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Staged attachments (picked files + captured screenshots, one representation — see
  // `lib/api/attachments.ts`) not yet sent. `attachmentError` surfaces a client-side cap
  // violation (count/size) inline rather than silently dropping the attachment — mirrors how
  // `voiceError` reports a mic-permission failure below.
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  // Mirrors `attachments` for the unmount-only cleanup effect below, which must always revoke
  // whatever is CURRENTLY staged without re-running (and re-registering a new cleanup closure)
  // on every attach/remove — a plain ref read in the cleanup avoids a stale closure without
  // making the effect depend on `attachments` itself.
  const attachmentsRef = useRef<PendingAttachment[]>(attachments);
  attachmentsRef.current = attachments;

  // Revoke every staged attachment's object URL on unmount so a closed/navigated-away composer
  // never leaks blob URLs — the same cleanup discipline the mic-stream effect below already
  // applies to `MediaStream` tracks.
  useEffect(() => {
    return () => {
      for (const attachment of attachmentsRef.current) {
        if (attachment.previewUrl) {
          URL.revokeObjectURL(attachment.previewUrl);
        }
      }
    };
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((current) => {
      const target = current.find((a) => a.id === id);
      if (target?.previewUrl) {
        URL.revokeObjectURL(target.previewUrl);
      }
      return current.filter((a) => a.id !== id);
    });
  }, []);

  const addAttachments = useCallback(
    (incoming: PendingAttachment[]) => {
      setAttachments((current) => {
        const merged = [...current, ...incoming];
        if (merged.length > MAX_ATTACHMENTS_PER_TURN) {
          setAttachmentError(t("attachments.tooMany", { max: MAX_ATTACHMENTS_PER_TURN }));
          return merged.slice(0, MAX_ATTACHMENTS_PER_TURN);
        }
        setAttachmentError(null);
        return merged;
      });
    },
    [t],
  );

  // Mic recording state machine — see `VoiceOrSendButton`. `mediaRecorderRef`/`streamRef` are the
  // live `MediaRecorder`/`MediaStream` for the in-progress recording (browser-only APIs, so both
  // are only ever touched from event handlers/effects, never at render time); `audioChunksRef`
  // accumulates the recorder's `Blob` chunks until `stop()` fires.
  const [voiceState, setVoiceState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // Stop any still-live mic stream if the composer unmounts mid-recording (e.g. navigating away).
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((track) => {
        track.stop();
      });
    };
  }, []);

  const grow = autogrowTextarea;

  // The transcribed text lands in `draft` programmatically (not via the textarea's own `onChange`),
  // so the auto-grow above wouldn't otherwise run for it — re-run it whenever `draft` changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `grow` is a plain closure recreated every render; only `draft` changing should re-trigger the resize.
  useEffect(() => {
    if (textareaRef.current) {
      grow(textareaRef.current);
    }
  }, [draft]);

  // Shared by the text-submit path and the voice-confirm path: both end in "send this text +
  // these attachments, then clear the composer" — voice can't reuse `submit()` directly since
  // its text comes from an async transcription result, never from `draft`/a form event.
  const sendMessage = useCallback(
    (text: string, toSend: PendingAttachment[]) => {
      const trimmed = text.trim();
      if (trimmed.length === 0 || streaming) {
        return;
      }
      onSend(
        trimmed,
        toSend.map((a) => a.input),
      );
      for (const attachment of toSend) {
        if (attachment.previewUrl) {
          URL.revokeObjectURL(attachment.previewUrl);
        }
      }
      setAttachments([]);
      setAttachmentError(null);
      setDraft("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    },
    [streaming, onSend],
  );

  const submit = (e?: FormEvent): void => {
    e?.preventDefault();
    sendMessage(draft, attachments);
  };

  // Set by `cancelRecording` right before `MediaRecorder.stop()` so `onstop` (which always fires,
  // cancel or confirm) knows to discard the captured audio instead of transcribing it — the
  // recorder has no native "abort without emitting" mode, only `stop()`.
  const recordingCancelledRef = useRef(false);

  const finishRecording = useCallback(
    async (mimeType: string) => {
      if (recordingCancelledRef.current) {
        audioChunksRef.current = [];
        setVoiceState("idle");
        return;
      }
      setVoiceState("transcribing");
      const blob = new Blob(audioChunksRef.current, { type: mimeType });
      audioChunksRef.current = [];
      try {
        const transcript = await transcribeAudio(blob, locale);
        // Confirm = transcribe + send immediately (no separate review step) — the product
        // decision this composer's voice flow is built to, matching the confirm/✓ affordance.
        sendMessage(transcript.text, attachments);
      } catch {
        setVoiceError(t("voiceError"));
      } finally {
        setVoiceState("idle");
      }
    },
    [locale, t, sendMessage, attachments],
  );

  const startRecording = useCallback(async () => {
    if (streaming || voiceState !== "idle") {
      return;
    }
    setVoiceError(null);
    recordingCancelledRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
        (candidate) =>
          typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(candidate),
      );
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      audioChunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => {
          track.stop();
        });
        streamRef.current = null;
        void finishRecording(recorder.mimeType || mimeType || "audio/webm");
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setVoiceState("recording");
    } catch {
      // The user denied the permission prompt (or no mic is available). Unlike other non-fatal
      // UI dismissals in this file (e.g. screenshot capture cancellation, which stays silent), a
      // sighted user still gets a visual signal here — the mic button reverts to idle — but a
      // screen-reader user gets nothing from that alone, so this one specific path is announced
      // via the same `voiceError`/`role="alert"` region `finishRecording`'s catch already uses.
      // This is a deliberate, narrow exception, not a precedent for toasting every dismissal.
      setVoiceError(t("micPermissionDenied"));
      setVoiceState("idle");
    }
  }, [streaming, voiceState, finishRecording, t]);

  /** ✓ — stop and transcribe+send (the confirm path `finishRecording` runs by default). */
  const confirmRecording = useCallback((): void => {
    if (mediaRecorderRef.current && voiceState === "recording") {
      recordingCancelledRef.current = false;
      mediaRecorderRef.current.stop();
    }
  }, [voiceState]);

  /** ✕ — stop and discard; `finishRecording` sees the cancelled flag and never calls
   * `/voice/transcribe`. */
  const cancelRecording = useCallback((): void => {
    if (mediaRecorderRef.current && voiceState === "recording") {
      recordingCancelledRef.current = true;
      mediaRecorderRef.current.stop();
    }
  }, [voiceState]);

  const hasText = draft.trim().length > 0;
  const canSend = hasText;

  const onFilesPicked = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) {
        return;
      }
      const staged = await Promise.all(Array.from(files).map(fileToPendingAttachment));
      addAttachments(staged);
    },
    [addAttachments],
  );

  // A long paste becomes a removable attachment chip instead of flooding the draft — mirrors
  // Claude.ai's own composer behavior. A paste that also carries a `File` (e.g. copying an image)
  // is left to the browser's native paste-as-text handling untouched; this only intercepts a
  // plain-text clipboard payload once it crosses the length/line threshold.
  const onPaste = useCallback(
    (e: ClipboardEvent<HTMLTextAreaElement>) => {
      if (e.clipboardData.files.length > 0) {
        return;
      }
      const text = e.clipboardData.getData("text/plain");
      if (!shouldAttachPastedText(text)) {
        return;
      }
      e.preventDefault();
      void pastedTextToPendingAttachment(text).then((attachment) => addAttachments([attachment]));
    },
    [addAttachments],
  );

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <form onSubmit={submit}>
        <div
          className={cn(
            "flex flex-col gap-1 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-2 transition-colors focus-within:border-[var(--accent)]",
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_ATTACHMENT_MEDIA_TYPES}
            onChange={(e) => {
              void onFilesPicked(e.target.files);
              e.target.value = "";
            }}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          />

          {attachments.length > 0 ? (
            <div className="flex flex-wrap gap-2 px-1 pt-1">
              {attachments.map((attachment) => (
                <AttachmentPreview
                  key={attachment.id}
                  attachment={attachment}
                  removeLabel={t("attachments.remove")}
                  onRemove={() => removeAttachment(attachment.id)}
                />
              ))}
            </div>
          ) : null}

          {attachmentError ? (
            <p
              role="alert"
              className="flex items-center gap-1.5 px-2 pt-1 text-[12px] text-[var(--warn)]"
            >
              <WarningIcon size={13} weight="regular" aria-hidden />
              {attachmentError}
            </p>
          ) : null}

          <div className="flex items-end gap-2">
            <FeatureMenu
              label={t("featureMenu")}
              addFilesLabel={t("featureMenuItems.addFiles")}
              webSearchEnabled={webSearchEnabled}
              onWebSearchEnabledChange={onWebSearchEnabledChange}
              deepResearch={deepResearch}
              deepResearchLocked={deepResearchLocked}
              onAddFiles={() => fileInputRef.current?.click()}
              onCaptureScreenshot={async () => {
                const dataUrl = await captureScreenshot();
                if (dataUrl) {
                  addAttachments([screenshotToPendingAttachment(dataUrl)]);
                }
              }}
            />

            <label htmlFor="miru-chat-composer" className="sr-only">
              {t("label")}
            </label>
            <textarea
              id="miru-chat-composer"
              ref={textareaRef}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                grow(e.target);
              }}
              onKeyDown={(e) => {
                // ⌘/Ctrl+Enter or plain Enter (no shift) sends; shift+Enter is a newline.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              onPaste={onPaste}
              rows={1}
              disabled={voiceState === "recording" || voiceState === "transcribing"}
              placeholder={voiceState === "recording" ? t("listening") : t("placeholder")}
              className={cn(
                "min-h-11 flex-1 resize-none overflow-y-hidden bg-transparent px-2 py-2.5",
                "text-[15px] text-[var(--text)] leading-relaxed placeholder:text-[var(--text-muted)]",
                // The wrapping pill already shows focus via `focus-within:border-[var(--accent)]`
                // above. The global focus-visible rule (globals.css) is deliberately unlayered
                // CSS, which always wins over a layered Tailwind utility regardless of
                // specificity — a plain `outline-none` can't override it, hence `!`.
                "!outline-none",
              )}
            />

            {streaming ? (
              <button
                type="button"
                onClick={onStop}
                aria-label={t("stop")}
                title={t("stop")}
                className={cn(
                  "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
                  "bg-[var(--surface-2)] text-[var(--text)] transition-colors hover:bg-[var(--border)]",
                  "active:scale-95",
                )}
              >
                <SquareIcon size={18} weight="fill" />
              </button>
            ) : voiceState === "recording" ? (
              <RecordingControls
                cancelLabel={t("voiceRecording.cancel")}
                confirmLabel={t("voiceRecording.confirm")}
                onCancel={cancelRecording}
                onConfirm={confirmRecording}
              />
            ) : (
              <>
                {!hasText && voiceState === "idle" ? (
                  <VoiceModeTriggerButton label={t("voiceMode")} onClick={onOpenVoiceMode} />
                ) : null}
                <VoiceOrSendButton
                  canSend={canSend}
                  voiceState={voiceState}
                  streaming={streaming}
                  onStartRecording={startRecording}
                  voiceLabel={t("voice")}
                  sendLabel={t("send")}
                />
              </>
            )}
          </div>

          {voiceError ? (
            <p
              role="alert"
              className="flex items-center gap-1.5 px-2 text-[12px] text-[var(--warn)]"
            >
              <WarningIcon size={13} weight="regular" aria-hidden />
              {voiceError}
            </p>
          ) : null}

          <div className="flex min-w-0 items-center gap-1 px-1 pb-0.5">
            <ModelMenu
              label={t("model")}
              byokActive={byokActive}
              selectedId={modelOverride}
              onSelect={onModelOverrideChange}
            />
            <ToneMenu selected={toneOverride} onSelect={onToneOverrideChange} />
          </div>
        </div>
      </form>
      <p className="mt-2 text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
        {tDisc("text")}
      </p>
    </div>
  );
}
