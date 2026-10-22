"use client";

import { WaveformIcon, XIcon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { audioBase64ToObjectUrl, speakText, transcribeAudio } from "@/lib/api/voice";
import { cn } from "@/lib/cn";
import { markdownToSpeech } from "@/lib/markdown-to-speech";

/** The continuous, hands-free voice-conversation phase — drives both the orb's visual state and
 * the caption text below it. Cycles LISTENING → TRANSCRIBING → SENDING → SPEAKING → back to
 * LISTENING automatically forever, until the user taps ✕ (any phase) — see the module docstring
 * on `VoiceModeOverlay` for the full state machine and the product decisions behind it. */
type VoicePhase = "listening" | "transcribing" | "sending" | "speaking";

/** How long the mic must sit below `_SILENCE_RMS_THRESHOLD` before LISTENING auto-advances to
 * TRANSCRIBING — long enough that a normal mid-sentence pause doesn't cut the user off, short
 * enough that the cycle still feels responsive. Tuned empirically; not backed by a spec. */
const _SILENCE_DURATION_MS = 1200;
/** RMS amplitude (0–1 scale, matching `AnalyserNode.getByteTimeDomainData`'s normalized output)
 * below which the mic is considered silent. Background-noise-tolerant, not a voice-activity
 * detector — good enough for "has the user stopped talking", not diarization-grade. */
const _SILENCE_RMS_THRESHOLD = 0.02;
/** A hard ceiling so a stuck-open mic (e.g. a silence gate that never trips in a noisy room)
 * can't record forever — matches the STT edge's own `_MAX_AUDIO_BYTES` intent of bounding a
 * single utterance, just time-based instead of byte-based. */
const _MAX_RECORDING_MS = 30_000;

/**
 * The fully separate, hands-free continuous voice-conversation overlay — distinct from the
 * composer's own dictation mic (`RecordingControls`/`MicrophoneIcon`). Opened by the composer's
 * waveform trigger (`onOpenVoiceMode`), this owns its own listen → transcribe → send → speak →
 * relisten cycle with NO per-turn tap required, per the product decision this was built to: it
 * keeps cycling automatically until the user taps ✕, using client-side silence detection
 * (`AnalyserNode` RMS + a timeout) to know when the user stopped talking — never a fixed-duration
 * recording window. There is deliberately no barge-in in this pass: while SPEAKING plays the
 * answer's TTS audio, the mic stays closed; LISTENING only reopens once playback finishes.
 *
 * `runTurn` is `ChatClient.runVoiceTurn` — the exact same turn pipeline (persistence, guardrails,
 * thread adoption) a typed send uses, just returning the final answer text instead of leaving it
 * in the live overlay, since this component has no visible transcript to read it from.
 *
 * The non-trading caveat is never audio-only (governance: never optional/hidden) — every SPEAKING
 * phase shows the caveat text alongside the caption, not just voices it.
 */
export function VoiceModeOverlay({
  onClose,
  runTurn,
}: Readonly<{
  onClose: () => void;
  runTurn: (message: string) => Promise<string | null>;
}>): ReactNode {
  const t = useTranslations("chat.voiceMode");
  const locale = useLocale();
  const reduceMotion = useReducedMotion();

  const [phase, setPhase] = useState<VoicePhase>("listening");
  const [caption, setCaption] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Guards every async continuation (silence-timeout fire, transcribe response, turn response,
  // audio `onended`) against running after the user has already closed the overlay — `onClose`
  // unmounts this component, but a promise already in flight has no other way to know to stop.
  const closedRef = useRef(false);

  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxDurationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rafRef = useRef<number | null>(null);
  const playbackRef = useRef<HTMLAudioElement | null>(null);
  const playbackUrlRef = useRef<string | null>(null);
  // `startListening` and `handleRecordingComplete` call each other (listen → complete → relisten
  // → …) — an inherent cycle in a callback graph, not something a dependency array can express.
  // Each is defined as a stable ref-backed function below (`callStable`) so the OTHER side can
  // call through the ref instead of needing the callback itself as a `useCallback` dependency —
  // this breaks the ordering problem for real, rather than suppressing the lint that (correctly)
  // flags the literal cycle.
  const handleRecordingCompleteRef = useRef<(mimeType: string) => Promise<void>>(async () => {});
  const startListeningRef = useRef<() => Promise<void>>(async () => {});

  const teardownRecording = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (maxDurationTimerRef.current) {
      clearTimeout(maxDurationTimerRef.current);
      maxDurationTimerRef.current = null;
    }
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => {
      track.stop();
    });
    streamRef.current = null;
    if (audioContextRef.current) {
      void audioContextRef.current.close();
      audioContextRef.current = null;
    }
    mediaRecorderRef.current = null;
  }, []);

  const teardownPlayback = useCallback(() => {
    playbackRef.current?.pause();
    playbackRef.current = null;
    if (playbackUrlRef.current) {
      URL.revokeObjectURL(playbackUrlRef.current);
      playbackUrlRef.current = null;
    }
  }, []);

  // Full cleanup on unmount (covers both the explicit ✕ path below and an unexpected unmount,
  // e.g. route navigation while the overlay is open) — a live mic stream or playing `<audio>`
  // must never survive the component that opened it.
  useEffect(() => {
    return () => {
      closedRef.current = true;
      teardownRecording();
      teardownPlayback();
    };
  }, [teardownRecording, teardownPlayback]);

  const startListening = useCallback(async () => {
    if (closedRef.current) {
      return;
    }
    setError(null);
    setCaption("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (closedRef.current) {
        // Closed while the permission prompt was pending — release immediately, never record.
        stream.getTracks().forEach((track) => {
          track.stop();
        });
        return;
      }
      streamRef.current = stream;
      setPhase("listening");

      const AudioContextCtor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      const audioContext = new AudioContextCtor();
      audioContextRef.current = audioContext;
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const samples = new Uint8Array(analyser.frequencyBinCount);

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
      const stopAndTranscribe = (): void => {
        if (recorder.state !== "inactive") {
          recorder.stop();
        }
      };
      recorder.onstop = () => {
        teardownRecording();
        void handleRecordingCompleteRef.current(recorder.mimeType || mimeType || "audio/webm");
      };
      mediaRecorderRef.current = recorder;
      recorder.start();

      maxDurationTimerRef.current = setTimeout(stopAndTranscribe, _MAX_RECORDING_MS);

      // Poll RMS amplitude every animation frame; a silence run of `_SILENCE_DURATION_MS` (timer
      // reset on every LOUD frame) auto-stops the recorder — this is the "no per-turn tap needed"
      // mechanism the whole overlay is built around.
      const tick = (): void => {
        analyser.getByteTimeDomainData(samples);
        let sumSquares = 0;
        for (const sample of samples) {
          const normalized = (sample - 128) / 128;
          sumSquares += normalized * normalized;
        }
        const rms = Math.sqrt(sumSquares / samples.length);
        if (rms > _SILENCE_RMS_THRESHOLD) {
          if (silenceTimerRef.current) {
            clearTimeout(silenceTimerRef.current);
            silenceTimerRef.current = null;
          }
        } else if (!silenceTimerRef.current) {
          silenceTimerRef.current = setTimeout(stopAndTranscribe, _SILENCE_DURATION_MS);
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    } catch {
      if (!closedRef.current) {
        setError(t("micPermissionDenied"));
      }
    }
  }, [t, teardownRecording]);

  // Keep the ref in sync every render so `recorder.onstop` above always calls the CURRENT
  // `startListening` closure (fresh `t`/`teardownRecording`), never a stale one captured at
  // mount — a plain assignment during render is safe here since refs are mutable containers, not
  // reactive state, and nothing reads this ref during the render phase itself.
  startListeningRef.current = startListening;

  const handleRecordingComplete = useCallback(
    async (mimeType: string) => {
      if (closedRef.current) {
        return;
      }
      const blob = new Blob(audioChunksRef.current, { type: mimeType });
      audioChunksRef.current = [];
      if (blob.size === 0) {
        // Nothing was actually captured (e.g. immediate silence-timeout with no speech at all) —
        // just relisten rather than round-tripping an empty/near-empty clip to STT.
        void startListeningRef.current();
        return;
      }
      setPhase("transcribing");
      let text: string;
      try {
        const transcript = await transcribeAudio(blob, locale);
        text = transcript.text.trim();
      } catch {
        if (closedRef.current) {
          return;
        }
        setError(t("transcribeError"));
        void startListeningRef.current();
        return;
      }
      if (closedRef.current) {
        return;
      }
      if (text.length === 0) {
        // Silence-gated but STT still found nothing sayable — relisten without bothering the
        // agent with an empty turn.
        void startListeningRef.current();
        return;
      }
      setCaption(text);
      setPhase("sending");
      let answer: string | null;
      try {
        answer = await runTurn(text);
      } catch {
        answer = null;
      }
      if (closedRef.current) {
        return;
      }
      if (answer === null || answer.trim().length === 0) {
        setError(t("sendError"));
        void startListeningRef.current();
        return;
      }
      setCaption(answer);
      setPhase("speaking");
      try {
        const spoken = markdownToSpeech(answer);
        const response = await speakText(spoken, locale);
        if (closedRef.current) {
          return;
        }
        const url = audioBase64ToObjectUrl(response.audio_base64, response.content_type);
        playbackUrlRef.current = url;
        const audio = new Audio(url);
        audio.onended = () => {
          teardownPlayback();
          if (!closedRef.current) {
            void startListeningRef.current();
          }
        };
        playbackRef.current = audio;
        await audio.play();
      } catch {
        // TTS failed (network/provider) — the caption already shows the answer text, so nothing
        // is lost; just relisten rather than getting stuck on a phase with no audio to finish.
        if (!closedRef.current) {
          void startListeningRef.current();
        }
      }
    },
    [locale, t, runTurn, teardownPlayback],
  );

  // Same ref-sync as `startListeningRef` above — breaks the other half of the mutual-recursion
  // cycle (`startListening`'s `recorder.onstop` calls back into this).
  handleRecordingCompleteRef.current = handleRecordingComplete;

  // Kick off the very first listen the moment the overlay mounts — no initial tap needed, since
  // opening voice mode at all IS the user's intent to start talking. Reads `startListeningRef`
  // rather than depending on `startListening` directly — the ref is already kept current every
  // render (above), and this effect only needs to fire once, at mount.
  useEffect(() => {
    void startListeningRef.current();
  }, []);

  const handleClose = useCallback(() => {
    closedRef.current = true;
    teardownRecording();
    teardownPlayback();
    onClose();
  }, [teardownRecording, teardownPlayback, onClose]);

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 px-6",
        "bg-[var(--bg)]",
      )}
      role="dialog"
      aria-modal="true"
      aria-label={t("close")}
    >
      <button
        type="button"
        onClick={handleClose}
        aria-label={t("close")}
        title={t("close")}
        className={cn(
          "absolute top-4 right-4 inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
          "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
          "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        )}
      >
        <XIcon size={22} weight="regular" />
      </button>

      <VoiceOrb phase={phase} reduceMotion={reduceMotion} />

      <p className="text-[15px] text-[var(--text-muted)]" aria-live="off">
        {t(`phase.${phase}`)}
      </p>

      {caption ? (
        <p className="max-w-md text-center text-[17px] text-[var(--text)] leading-relaxed">
          {caption}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function orbPulseSpeed(phase: VoicePhase): number {
  if (phase === "listening") return 1.4;
  if (phase === "speaking") return 0.9;
  return 2.2;
}

/** The pulsing orb visualization — color/motion intensity reflects `phase` (governance: this is
 * chrome-only motion, never a price/score, so it isn't bound by the non-trading motion
 * restrictions on magnitude-scaled animation; it's decorative state feedback, same category as
 * the composer's mic waveform pulse). `--accent` teal only — never a signed up/down token. */
function VoiceOrb({
  phase,
  reduceMotion,
}: Readonly<{
  phase: VoicePhase;
  reduceMotion: boolean | null;
}>): ReactNode {
  const pulseSpeed = orbPulseSpeed(phase);
  return (
    <div className="relative flex size-40 items-center justify-center">
      <AnimatePresence>
        {!reduceMotion ? (
          <motion.div
            className="absolute inset-0 rounded-full bg-[var(--accent)]"
            style={{ opacity: 0.18 }}
            animate={{ scale: [1, 1.25, 1] }}
            transition={{
              duration: pulseSpeed,
              repeat: Number.POSITIVE_INFINITY,
              ease: "easeInOut",
            }}
          />
        ) : null}
      </AnimatePresence>
      <div
        className={cn(
          "relative flex size-28 items-center justify-center rounded-full",
          "bg-[color-mix(in_srgb,var(--accent)_85%,var(--bg))] text-[var(--bg)]",
        )}
      >
        <WaveformIcon size={36} weight="regular" />
      </div>
    </div>
  );
}
