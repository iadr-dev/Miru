"use client";

import {
  ArrowUpIcon,
  CameraIcon,
  CaretDownIcon,
  CheckIcon,
  CircleNotchIcon,
  FileIcon,
  GlobeIcon,
  MagnifyingGlassIcon,
  MicrophoneIcon,
  PaperclipIcon,
  PlusIcon,
  SquareIcon,
  WarningIcon,
  WaveformIcon,
  XIcon,
} from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
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
import { useRouter } from "@/i18n/navigation";
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
import { cn } from "@/lib/cn";

/** A model catalog entry offered by the model-selector dropdown. */
type ModelOption = {
  id: string;
  label: string;
};

/** A provider category group in the model-selector dropdown, keyed to five of the providers this
 * backend's `src/llm/registry.py` has real `LLMClient` implementations for today — `groq` and
 * `cerebras` are excluded because those two are internal-only cheap roles for
 * classification/subagent fan-out, never a user-facing chat model. */
type ModelCategory = {
  key: "anthropic" | "openai" | "gemini" | "openrouter" | "grok" | "deepseek" | "ollama";
  models: ModelOption[];
};

// Hardcoded UI catalog — not fetched from anywhere. Every `id` is a full `"provider/model"`
// string matching `backend/src/llm/registry.py`'s own format (the same shape its
// `AITW_MODEL_ROLE_<ROLE>` env-var overrides use), since selecting an item here sends this exact
// string as `Thread.model_override` via `PATCH /threads/{id}` — see `ModelMenu` below. Every
// provider/model id was live-verified against its provider's real model-list endpoint + a real
// chat() call through this backend (see session notes) — entries that didn't resolve to a real,
// chat-callable model (a fabricated "Claude Sonnet 4.8", a non-chat "gpt-5.4-pro" completions-only
// model, "gpt-oss-20b" which OpenAI's own API doesn't serve, a bare "gemini-3.1" missing its real
// "-pro-preview" suffix, and OpenRouter's DeepSeek/Grok ids that didn't match any real listing)
// were corrected to ids that actually work.
const MODEL_CATEGORIES: ModelCategory[] = [
  {
    key: "anthropic",
    models: [
      { id: "anthropic/claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
      { id: "anthropic/claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
      { id: "anthropic/claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "anthropic/claude-opus-4-6", label: "Claude Opus 4.6" },
      { id: "anthropic/claude-opus-4-8", label: "Claude Opus 4.8" },
    ],
  },
  {
    key: "openai",
    models: [
      { id: "openai/gpt-4o-mini", label: "GPT-4o mini" },
      { id: "openai/gpt-5.4", label: "GPT-5.4" },
      { id: "openai/gpt-5.4-mini", label: "GPT-5.4 mini" },
      // "gpt-5.6" bare doesn't exist — only the "-sol"/"-terra"/"-luna" variants do (live-verified);
      // luna was the fastest of the three in a head-to-head latency check with near-identical
      // reasoning depth, so it represents "GPT-5.6" here.
      { id: "openai/gpt-5.6-luna", label: "GPT-5.6" },
    ],
  },
  {
    key: "gemini",
    models: [
      { id: "gemini/gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
      { id: "gemini/gemini-3.5-flash-lite", label: "Gemini 3.5 Flash-Lite" },
      { id: "gemini/gemini-3.5-flash", label: "Gemini 3.5 Flash" },
      { id: "gemini/gemini-3.6-flash", label: "Gemini 3.6 Flash" },
    ],
  },
  {
    key: "openrouter",
    models: [
      { id: "openrouter/openrouter/auto", label: "openrouter/auto" },
      { id: "openrouter/meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B" },
    ],
  },
  // Grok and DeepSeek are reached through OpenRouter too (ids are `openrouter/<vendor>/<model>`);
  // they get their own groups because each vendor now has enough models to crowd the one list.
  {
    key: "grok",
    models: [
      { id: "openrouter/~x-ai/grok-latest", label: "Grok latest" },
      { id: "openrouter/x-ai/grok-4.7", label: "Grok 4.7" },
      { id: "openrouter/x-ai/grok-4.6", label: "Grok 4.6" },
      { id: "openrouter/x-ai/grok-4.5", label: "Grok 4.5" },
      { id: "openrouter/x-ai/grok-4.20", label: "Grok 4.20" },
    ],
  },
  {
    key: "deepseek",
    models: [
      { id: "openrouter/deepseek/deepseek-chat-v3.1", label: "DeepSeek V3.1" },
      { id: "openrouter/deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash" },
      { id: "openrouter/deepseek/deepseek-v4-pro", label: "DeepSeek V4 Pro" },
    ],
  },
  {
    key: "ollama",
    models: [{ id: "ollama/qwen3.6:latest", label: "Qwen 3.6:latest" }],
  },
];

/**
 * Prompts the browser's native "Share your screen" picker via `getDisplayMedia` — the Screen
 * Capture API, the only web platform mechanism for arbitrary screen capture (there is no
 * separate "take a screenshot" API; this is the same permission UI Chrome/Claude Desktop show
 * for screen-share flows). Once the user grants a source, grabs exactly one still frame off the
 * resulting video track by drawing it to an offscreen `<canvas>`, then immediately stops every
 * track — a live feed is never needed, only a single frame.
 *
 * Resolves to a data URL on success, or `null` if the user cancels/denies the picker or capture
 * otherwise fails — callers treat `null` as a silent no-op (this codebase doesn't toast on
 * non-fatal UI dismissals).
 */
async function captureScreenshot(): Promise<string | null> {
  let stream: MediaStream | null = null;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    const [track] = stream.getVideoTracks();
    if (!track) {
      return null;
    }
    const video = document.createElement("video");
    video.srcObject = stream;
    await video.play();
    // A freshly-played video may report its first frame a tick late; `requestVideoFrameCallback`
    // (falling back to a microtask) ensures the canvas draw below isn't racing an empty frame.
    await new Promise<void>((resolve) => {
      if ("requestVideoFrameCallback" in video) {
        (video as HTMLVideoElement).requestVideoFrameCallback(() => resolve());
      } else {
        resolve();
      }
    });
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx || canvas.width === 0 || canvas.height === 0) {
      return null;
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  } catch {
    // User cancelled/denied the picker, or the platform doesn't support it — no error toast,
    // matching how this codebase treats other non-fatal UI dismissals.
    return null;
  } finally {
    stream?.getTracks().forEach((t) => {
      t.stop();
    });
  }
}

/** Resolves a `model_override` string (as stored on `Thread`) to its catalog entry, or `null`
 * when unset — the caller renders `null` as the translated "server default" label rather than a
 * hardcoded English one, and never as "Auto" (that string names a real, distinct, selectable
 * OpenRouter catalog entry — `openrouter/auto` — so it can't also mean "no override chosen"
 * without the two being confusable in the same menu). A thread overridden to a model this build's
 * catalog doesn't (yet) list still shows a real trigger label via the raw id rather than
 * silently falling back to the default, which would misrepresent what the thread is actually
 * configured to use. */
/** The provider half of a catalog id ("openrouter/x-ai/grok-4.7" -> "openrouter"). */
export function modelProvider(modelId: string): string {
  return modelId.split("/")[0] ?? "";
}

/** The provider the backend uses when a thread has picked no model (its AGENT role default). */
export const DEFAULT_MODEL_PROVIDER = "anthropic";

/** The first catalog model served by any of `providers` - the model to switch to when the current
 * one belongs to a provider the caller has no key for. */
export function firstModelFor(providers: string[]): string | null {
  for (const category of MODEL_CATEGORIES) {
    const match = category.models.find((m) => providers.includes(modelProvider(m.id)));
    if (match) {
      return match.id;
    }
  }
  return null;
}

function modelOptionFor(modelOverride: string | null | undefined): ModelOption | null {
  if (!modelOverride) {
    return null;
  }
  for (const category of MODEL_CATEGORIES) {
    const match = category.models.find((model) => model.id === modelOverride);
    if (match) {
      return match;
    }
  }
  return { id: modelOverride, label: modelOverride };
}

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
  webSearchEnabled,
  onWebSearchEnabledChange,
  deepResearch,
  deepResearchLocked,
  byokProviders,
  onOpenVoiceMode,
}: {
  streaming: boolean;
  onSend: (text: string, attachments: AttachmentInput[]) => void;
  onStop: () => void;
  modelOverride: string | null;
  onModelOverrideChange: (modelId: string) => void;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  /** Null when the caller's plan has no deep research (the menu row is then hidden). */
  deepResearch: { enabled: boolean; onChange: (enabled: boolean) => void } | null;
  /** True when the caller's plan has no deep research: the row is shown locked, with the way to
   * upgrade, rather than hidden. */
  deepResearchLocked: boolean;
  /** Providers the caller has their own key for, or null when they have none. With keys, only
   * those providers' models can be used (they run on the caller's key). */
  byokProviders: string[] | null;
  onOpenVoiceMode: () => void;
}): ReactNode {
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

  // Grows with content up to ~45% of the viewport height (matching the Claude-style composer —
  // the box itself expands, no inner scrollbar, until it's genuinely too tall to keep growing).
  // Only past that cap does the textarea's own overflow ever kick in.
  const grow = (el: HTMLTextAreaElement): void => {
    const cap = Math.round(window.innerHeight * 0.45);
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, cap);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > cap ? "auto" : "hidden";
  };

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

          <div className="flex items-center px-1 pb-0.5">
            <ModelMenu
              label={t("model")}
              selected={modelOptionFor(modelOverride)}
              allowedProviders={byokProviders}
              onSelect={onModelOverrideChange}
            />
          </div>
        </div>
      </form>
      <p className="mt-2 text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
        {tDisc("text")}
      </p>
    </div>
  );
}

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
}: {
  sendLabel: string;
  reduceMotion: boolean | null;
}): ReactNode {
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
function RecordingControls({
  cancelLabel,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  cancelLabel: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}): ReactNode {
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
function VoiceModeTriggerButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}): ReactNode {
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
}: {
  voiceLabel: string;
  reduceMotion: boolean | null;
}): ReactNode {
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
}: {
  voiceLabel: string;
  streaming: boolean;
  reduceMotion: boolean | null;
  onStartRecording: () => void;
}): ReactNode {
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
function VoiceOrSendButton({
  canSend,
  voiceState,
  streaming,
  onStartRecording,
  voiceLabel,
  sendLabel,
}: {
  canSend: boolean;
  voiceState: "idle" | "recording" | "transcribing";
  streaming: boolean;
  onStartRecording: () => void;
  voiceLabel: string;
  sendLabel: string;
}): ReactNode {
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

/** The inline preview for one staged (not-yet-sent) attachment, with a small "×" remove button —
 * an image shows a thumbnail (plain `<img>`, not `next/image`: the source is either an ephemeral
 * `data:`/blob object URL that was never going to be optimized/served by Next's image pipeline
 * anyway), a long paste shows a scrollable content preview + a "Pasted" badge (`PastedTextPreview`
 * below), and any other document shows a filename chip with a file icon instead, since there's no
 * visual thumbnail to render for a `.docx`/`.pdf`/etc. */
function AttachmentPreview({
  attachment,
  removeLabel,
  onRemove,
}: {
  attachment: PendingAttachment;
  removeLabel: string;
  onRemove: () => void;
}): ReactNode {
  if (attachment.pastedText !== undefined) {
    return (
      <PastedTextPreview attachment={attachment} removeLabel={removeLabel} onRemove={onRemove} />
    );
  }

  if (attachment.isImage && attachment.previewUrl) {
    return (
      <div className="relative inline-flex size-16 shrink-0">
        {/* biome-ignore lint/performance/noImgElement: `src` is an ephemeral in-memory data:/blob
         * URL (a screen capture or a locally-picked file) — there is nothing for next/image's
         * remote-optimization pipeline to do with it. */}
        <img
          src={attachment.previewUrl}
          alt=""
          className={cn(
            "size-16 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] object-cover",
          )}
        />
        <button
          type="button"
          onClick={onRemove}
          aria-label={removeLabel}
          title={removeLabel}
          className={cn(
            "absolute -top-1.5 -right-1.5 inline-flex size-5 items-center justify-center rounded-full",
            "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)]",
            "transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
          )}
        >
          <XIcon size={12} weight="regular" />
        </button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "inline-flex max-w-48 items-center gap-1.5 rounded-[var(--radius-card)] border border-[var(--border)]",
        "bg-[var(--surface-2)] px-2.5 py-2",
      )}
    >
      <FileIcon size={16} weight="regular" className="shrink-0 text-[var(--text-muted)]" />
      <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
        {attachment.filename}
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={removeLabel}
        title={removeLabel}
        className={cn(
          "inline-flex size-5 shrink-0 items-center justify-center rounded-full",
          "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--text)]",
        )}
      >
        <XIcon size={12} weight="regular" />
      </button>
    </div>
  );
}

/** The long-paste-turned-attachment card: a small scrollable content preview (never full-height —
 * this is a glance, not a reader) with a "Pasted" pill badge in its corner, matching the reference
 * pattern (a long paste collapses to a chip rather than flooding the draft). The character count
 * gives a size cue without needing to expand the preview. */
function PastedTextPreview({
  attachment,
  removeLabel,
  onRemove,
}: {
  attachment: PendingAttachment;
  removeLabel: string;
  onRemove: () => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const text = attachment.pastedText ?? "";

  return (
    <div
      className={cn(
        "relative flex w-44 flex-col gap-1.5 rounded-[var(--radius-card)] border border-[var(--border)]",
        "bg-[var(--surface-2)] p-2.5",
      )}
    >
      <button
        type="button"
        onClick={onRemove}
        aria-label={removeLabel}
        title={removeLabel}
        className={cn(
          "absolute -top-1.5 -right-1.5 inline-flex size-5 items-center justify-center rounded-full",
          "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)]",
          "transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        )}
      >
        <XIcon size={12} weight="regular" />
      </button>
      <p
        className={cn(
          "line-clamp-4 whitespace-pre-line text-[11px] text-[var(--text-muted)] leading-snug",
        )}
      >
        {text}
      </p>
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "inline-flex items-center rounded-[var(--radius-pill)] border border-[var(--border)]",
            "px-2 py-0.5 font-medium text-[10px] text-[var(--text-muted)] uppercase tracking-wide",
          )}
        >
          {t("attachments.pasted")}
        </span>
        <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
          {t("attachments.pastedCharCount", { count: text.length })}
        </span>
      </div>
    </div>
  );
}

/** The `+` feature-menu trigger. "Add files or photos" opens the hidden file input (`onAddFiles`);
 * "Take a screenshot" and "Web search" were already wired for real — the CAPTURE step of a
 * screenshot is a pure-browser flow (`captureScreenshot`, above) with no backend dependency, and
 * web search is a real `web_search: bool` field `/agent/query` (and `regenerate`/`edit`) accept,
 * so "Web search" renders as a checkable toggle row rather than a disabled stub. */
function FeatureMenu({
  label,
  addFilesLabel,
  webSearchEnabled,
  onWebSearchEnabledChange,
  deepResearch,
  deepResearchLocked,
  onAddFiles,
  onCaptureScreenshot,
}: {
  label: string;
  addFilesLabel: string;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  deepResearch: { enabled: boolean; onChange: (enabled: boolean) => void } | null;
  deepResearchLocked: boolean;
  onAddFiles: () => void;
  onCaptureScreenshot: () => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            "data-[state=open]:bg-[var(--surface-2)] data-[state=open]:text-[var(--text)]",
          )}
        >
          <PlusIcon size={20} weight="regular" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-64 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          <FeatureMenuItem icon={PaperclipIcon} label={addFilesLabel} onSelect={onAddFiles} />
          <FeatureMenuItem
            icon={CameraIcon}
            label={t("featureMenuItems.screenshot")}
            onSelect={onCaptureScreenshot}
          />
          <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
          <FeatureMenuItem
            icon={GlobeIcon}
            label={t("featureMenuItems.webSearch")}
            checked={webSearchEnabled}
            onSelect={() => onWebSearchEnabledChange(!webSearchEnabled)}
          />
          {deepResearch !== null ? (
            <FeatureMenuItem
              icon={MagnifyingGlassIcon}
              label={t("featureMenuItems.deepResearch")}
              checked={deepResearch.enabled}
              onSelect={() => deepResearch.onChange(!deepResearch.enabled)}
            />
          ) : deepResearchLocked ? (
            <FeatureMenuItem
              icon={MagnifyingGlassIcon}
              label={t("featureMenuItems.deepResearchLocked")}
              onSelect={() => router.push("/settings/billing")}
            />
          ) : null}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** One `FeatureMenu` row. `disabled` (the "add files" stub) blocks `onSelect` and lets Radix's own
 * styling hook cover the dimmed look. A row that instead passes `checked` (a boolean, not just
 * `undefined`) is a real toggle — e.g. "Web search" — and shows a trailing checkmark exactly the
 * way `ModelMenu`'s selected-item row does below, rather than inventing a new active-state
 * convention. "Take a screenshot" passes neither: it's real but one-shot, not a toggle. */
function FeatureMenuItem({
  icon: Icon,
  label,
  disabled,
  checked,
  onSelect,
}: {
  icon: typeof PlusIcon;
  label: string;
  disabled?: boolean;
  checked?: boolean;
  onSelect?: () => void;
}): ReactNode {
  return (
    <DropdownMenu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex min-h-9 items-center gap-2.5 rounded-[var(--radius-button)] px-2.5",
        "text-[var(--text)] text-sm outline-none transition-colors",
        disabled
          ? "text-[var(--text-muted)]"
          : "cursor-pointer data-[highlighted]:bg-[var(--surface-2)]",
        "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
      )}
    >
      <Icon size={16} weight="regular" aria-hidden />
      <span className="flex-1">{label}</span>
      {checked ? (
        <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" aria-hidden />
      ) : null}
    </DropdownMenu.Item>
  );
}

/** The model-selector dropdown trigger + menu. Controlled by the caller (see `Composer`'s
 * docstring) — `selected` is derived from the thread's real `model_override`, and `onSelect`
 * reports the chosen catalog id upward for the caller to persist via `patchThread`. */
function ModelMenu({
  label,
  selected,
  allowedProviders,
  onSelect,
}: {
  label: string;
  selected: ModelOption | null;
  allowedProviders: string[] | null;
  onSelect: (modelId: string) => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const [open, setOpen] = useState(false);
  const selectedLabel = selected?.label ?? t("modelMenu.default");
  // A caller with their own keys can only run those providers' models.
  const visibleCategories = MODEL_CATEGORIES.map((category) => ({
    ...category,
    models:
      allowedProviders === null
        ? category.models
        : category.models.filter((m) => allowedProviders.includes(modelProvider(m.id))),
  })).filter((category) => category.models.length > 0);

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex h-8 items-center gap-1 rounded-[var(--radius-button)] px-2.5",
            "text-[12px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <span>{t("modelMenu.trigger", { model: selectedLabel })}</span>
          <CaretDownIcon size={12} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-56 rounded-[var(--radius-card)] border border-[var(--border)]",
            "max-h-[360px] overflow-y-auto bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          {visibleCategories.map((category, index) => (
            <div key={category.key}>
              {index > 0 ? (
                <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
              ) : null}
              <DropdownMenu.Label className="px-2.5 py-1 text-[11px] text-[var(--text-muted)]">
                {t(`modelMenu.category.${category.key}`)}
              </DropdownMenu.Label>
              {category.models.map((model) => (
                <DropdownMenu.Item
                  key={model.id}
                  onSelect={() => onSelect(model.id)}
                  className={cn(
                    "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                    "text-[var(--text)] text-sm outline-none transition-colors",
                    "data-[highlighted]:bg-[var(--surface-2)]",
                  )}
                >
                  <span className="flex-1">{model.label}</span>
                  {model.id === selected?.id ? (
                    <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
                  ) : null}
                </DropdownMenu.Item>
              ))}
            </div>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
