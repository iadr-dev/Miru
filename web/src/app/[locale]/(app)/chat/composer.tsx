"use client";

import {
  ArrowUpIcon,
  CameraIcon,
  CaretDownIcon,
  CheckIcon,
  CircleNotchIcon,
  GlobeIcon,
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
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
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
  key: "anthropic" | "openai" | "gemini" | "openrouter" | "ollama";
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
      { id: "openrouter/deepseek/deepseek-chat-v3.1", label: "DeepSeek V3.1" },
      { id: "openrouter/deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash" },
      { id: "openrouter/deepseek/deepseek-v4-pro", label: "DeepSeek V4 Pro" },
      { id: "openrouter/meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B" },
      { id: "openrouter/x-ai/grok-4.3", label: "Grok 4.3" },
      { id: "openrouter/x-ai/grok-4.20", label: "Grok 4.20" },
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
 * "Add files or photos" is still VISUAL ONLY — there is no upload endpoint anywhere in this API —
 * so it stays a genuinely-disabled, tooltipped "coming soon" control rather than wired to fake
 * behavior.
 *
 * "Take a screenshot" is wired for real: capturing a still frame is a pure browser API
 * (`captureScreenshot`, below) with no backend dependency. There is still no `/agent/query` field
 * for an image attachment, though, so a captured screenshot can be previewed and removed but not
 * actually sent — see `screenshot` state and the disabled-send-with-tooltip branch below for how
 * sending is blocked while one is attached, and why.
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
 */
export function Composer({
  streaming,
  onSend,
  onStop,
  modelOverride,
  onModelOverrideChange,
  webSearchEnabled,
  onWebSearchEnabledChange,
}: {
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  modelOverride: string | null;
  onModelOverrideChange: (modelId: string) => void;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const tDisc = useTranslations("disclaimer");
  const locale = useLocale();
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Data URL of a screenshot captured via `captureScreenshot()`, or `null` when none is attached.
  // There is no `POST /agent/query` field for an image attachment yet (see module docstring), so
  // an attached screenshot blocks sending rather than being silently dropped — losing an attachment
  // the user can see on screen without any signal would be far more confusing than a disabled send
  // button with a tooltip explaining why.
  const [screenshot, setScreenshot] = useState<string | null>(null);

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

  const submit = (e?: FormEvent): void => {
    e?.preventDefault();
    const text = draft.trim();
    if (text.length === 0 || streaming || screenshot) {
      return;
    }
    onSend(text);
    setDraft("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const finishRecording = useCallback(
    async (mimeType: string) => {
      setVoiceState("transcribing");
      const blob = new Blob(audioChunksRef.current, { type: mimeType });
      audioChunksRef.current = [];
      try {
        const transcript = await transcribeAudio(blob, locale);
        setDraft(transcript.text);
      } catch {
        setVoiceError(t("voiceError"));
      } finally {
        setVoiceState("idle");
      }
    },
    [locale, t],
  );

  const startRecording = useCallback(async () => {
    if (streaming || voiceState !== "idle") {
      return;
    }
    setVoiceError(null);
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

  const stopRecording = useCallback((): void => {
    if (mediaRecorderRef.current && voiceState === "recording") {
      mediaRecorderRef.current.stop();
    }
  }, [voiceState]);

  const hasText = draft.trim().length > 0;
  const canSend = hasText && !screenshot;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <form onSubmit={submit}>
        <div
          className={cn(
            "flex flex-col gap-1 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-2 transition-colors focus-within:border-[var(--accent)]",
          )}
        >
          {screenshot ? (
            <div className="px-1 pt-1">
              <ScreenshotPreview
                src={screenshot}
                removeLabel={t("screenshot.remove")}
                onRemove={() => setScreenshot(null)}
              />
              <p className="mt-1.5 px-1 text-[12px] text-[var(--text-muted)]">
                {t("screenshot.notSupported")}
              </p>
            </div>
          ) : null}

          <div className="flex items-end gap-2">
            <FeatureMenu
              label={t("featureMenu")}
              comingSoon={t("comingSoon")}
              webSearchEnabled={webSearchEnabled}
              onWebSearchEnabledChange={onWebSearchEnabledChange}
              onCaptureScreenshot={async () => {
                const dataUrl = await captureScreenshot();
                if (dataUrl) {
                  setScreenshot(dataUrl);
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
            ) : screenshot ? (
              // Sending is blocked outright while a screenshot is attached (no `/agent/query`
              // field exists for an image attachment — see module docstring) — a disabled send
              // button with an explanatory tooltip is more honest than silently dropping the
              // image on submit, which would look like the screenshot was sent when it wasn't.
              <button
                type="button"
                disabled
                aria-label={`${t("send")} (${t("screenshot.notSupported")})`}
                title={t("screenshot.notSupported")}
                className={cn(
                  "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
                  "bg-[var(--accent)] text-[var(--bg)] opacity-40",
                )}
              >
                <ArrowUpIcon size={20} weight="bold" />
              </button>
            ) : (
              <VoiceOrSendButton
                canSend={canSend}
                voiceState={voiceState}
                streaming={streaming}
                onStartRecording={startRecording}
                onStopRecording={stopRecording}
                voiceLabel={t("voice")}
                stopRecordingLabel={t("stopRecording")}
                sendLabel={t("send")}
              />
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

/** The trailing composer control. Morphs across four states:
 * - `canSend` (any non-whitespace draft): the real send button (➤).
 * - idle + empty draft: the waveform mic — clicking it starts a real `getUserMedia` recording
 *   (`onStartRecording`, wired by the caller; browser-only APIs live there, never here).
 * - `voiceState === "recording"`: a Stop button reusing the exact same visual language as the
 *   composer's `streaming`→Stop button (`SquareIcon` fill, same size/colors) for consistency.
 * - `voiceState === "transcribing"`: a brief disabled spinner while `/voice/transcribe` is in
 *   flight — the mic is already released by then (see the caller's `recorder.onstop`). */
function VoiceOrSendButton({
  canSend,
  voiceState,
  streaming,
  onStartRecording,
  onStopRecording,
  voiceLabel,
  stopRecordingLabel,
  sendLabel,
}: {
  canSend: boolean;
  voiceState: "idle" | "recording" | "transcribing";
  streaming: boolean;
  onStartRecording: () => void;
  onStopRecording: () => void;
  voiceLabel: string;
  stopRecordingLabel: string;
  sendLabel: string;
}): ReactNode {
  const reduceMotion = useReducedMotion();
  const [hovering, setHovering] = useState(false);

  return (
    <div className="relative size-11 shrink-0">
      <AnimatePresence initial={false} mode="wait">
        {canSend ? (
          <motion.button
            key="send"
            type="submit"
            aria-label={sendLabel}
            title={sendLabel}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
            className={cn(
              "absolute inset-0 inline-flex items-center justify-center rounded-[var(--radius-button)]",
              "bg-[var(--accent)] text-[var(--bg)] transition-opacity hover:opacity-90 active:scale-95",
            )}
          >
            <ArrowUpIcon size={20} weight="bold" />
          </motion.button>
        ) : voiceState === "recording" ? (
          <motion.button
            key="recording"
            type="button"
            onClick={onStopRecording}
            aria-label={stopRecordingLabel}
            title={stopRecordingLabel}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
            className={cn(
              "absolute inset-0 inline-flex items-center justify-center rounded-[var(--radius-button)]",
              "bg-[var(--accent)] text-[var(--bg)] transition-colors hover:opacity-90 active:scale-95",
            )}
          >
            <motion.span
              animate={reduceMotion ? { opacity: 1 } : { opacity: [1, 0.55, 1] }}
              transition={{ duration: 1.2, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
              className="inline-flex"
            >
              <SquareIcon size={18} weight="fill" />
            </motion.span>
          </motion.button>
        ) : voiceState === "transcribing" ? (
          <motion.button
            key="transcribing"
            type="button"
            disabled
            aria-label={voiceLabel}
            title={voiceLabel}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
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
        ) : (
          <motion.button
            key="voice"
            type="button"
            disabled={streaming}
            onClick={onStartRecording}
            aria-label={voiceLabel}
            title={voiceLabel}
            onHoverStart={() => setHovering(true)}
            onHoverEnd={() => setHovering(false)}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
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
              <WaveformIcon size={20} weight="regular" />
            </motion.span>
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}

/** The inline thumbnail preview for a captured-but-unsent screenshot, with a small "×" remove
 * button. Plain `<img>` (not `next/image`) — the source is a `data:` URL, an ephemeral in-memory
 * capture that was never going to be optimized/served by Next's image pipeline anyway. */
function ScreenshotPreview({
  src,
  removeLabel,
  onRemove,
}: {
  src: string;
  removeLabel: string;
  onRemove: () => void;
}): ReactNode {
  return (
    <div className="relative inline-flex size-16 shrink-0">
      {/* biome-ignore lint/performance/noImgElement: `src` is an ephemeral in-memory `data:` URL
       * from a one-off screen capture — there is nothing for next/image's remote-optimization
       * pipeline to do with it. */}
      <img
        src={src}
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

/** The `+` feature-menu trigger. "Add files or photos" is still genuinely `disabled` + tooltipped
 * "coming soon" — there is no upload endpoint anywhere in this API. "Take a screenshot" and "Web
 * search" are both wired for real: the CAPTURE step of a screenshot is a pure-browser flow
 * (`captureScreenshot`, above) with no backend dependency, and web search is now a real
 * `web_search: bool` field `/agent/query` (and `regenerate`/`edit`) accept — so "Web search"
 * renders as a checkable toggle row rather than a disabled stub. */
function FeatureMenu({
  label,
  comingSoon,
  webSearchEnabled,
  onWebSearchEnabledChange,
  onCaptureScreenshot,
}: {
  label: string;
  comingSoon: string;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  onCaptureScreenshot: () => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
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
          <FeatureMenuItem icon={PaperclipIcon} label={t("featureMenuItems.addFiles")} disabled />
          <p className="px-2.5 pt-0.5 pb-1.5 text-[11px] text-[var(--text-muted)]">{comingSoon}</p>
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
  onSelect,
}: {
  label: string;
  selected: ModelOption | null;
  onSelect: (modelId: string) => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const [open, setOpen] = useState(false);
  const selectedLabel = selected?.label ?? t("modelMenu.default");

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
          {MODEL_CATEGORIES.map((category, index) => (
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
