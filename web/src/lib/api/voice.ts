/** Voice transcription client — `POST /voice/transcribe` (STT). Edge-only: the backend never
 * creates a turn from this call, it just returns a transcript for the composer to show for
 * review/edit before the user presses Send (see `backend/src/api/routers/voice.py`'s docstring).
 *
 * Mirrors `backend/src/api/routers/voice.py::TranscribeRequest` (`{audio_base64, language}`) and
 * `backend/src/voice/models.py::Transcript` (`{text, language, segments, low_confidence}`).
 * Credentialed (session cookie), parsed with Zod at the boundary like every other client here.
 */
import { z } from "zod";
import { apiUrl } from "@/lib/api/config";

const transcriptSegmentSchema = z.object({
  text: z.string(),
  confidence: z.number().min(0).max(1),
});

export const transcriptSchema = z.object({
  text: z.string(),
  language: z.string(),
  segments: z.array(transcriptSegmentSchema).default([]),
  low_confidence: z.boolean().default(false),
});
export type Transcript = z.infer<typeof transcriptSchema>;

/** `POST /voice/transcribe` — base64-encodes `audio` and posts it with `language` (a locale code,
 * e.g. `"zh-TW"`/`"en"`, matching `TranscribeRequest.language`'s default). Throws on a non-2xx
 * response; the caller (the composer's recording flow) is responsible for catching and falling
 * back to idle without touching the draft. */
export async function transcribeAudio(audio: Blob, language: string): Promise<Transcript> {
  const audioBase64 = await blobToBase64(audio);
  const response = await fetch(apiUrl("/voice/transcribe"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ audio_base64: audioBase64, language }),
  });
  if (!response.ok) {
    throw new Error(`Transcribe failed (${response.status})`);
  }
  return transcriptSchema.parse(await response.json());
}

/** Mirrors `backend/src/api/routers/voice.py::SpeakResponse` — `audio_base64` decodes to raw bytes
 * of `content_type`, and `spoken_text` is the exact (caveat-guaranteed) text that was voiced. */
export const speakResponseSchema = z.object({
  audio_base64: z.string(),
  content_type: z.string(),
  spoken_text: z.string(),
});
export type SpeakResponse = z.infer<typeof speakResponseSchema>;

/** `POST /voice/speak` — TTS read-aloud (Premium+; a 403 `feature_not_entitled` surfaces as a
 * thrown error same as any other non-2xx, the caller decides how to show it). Mirrors
 * `voice.py::SpeakRequest` (`{text, language}`). */
export async function speakText(text: string, language: string): Promise<SpeakResponse> {
  const response = await fetch(apiUrl("/voice/speak"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ text, language }),
  });
  if (!response.ok) {
    throw new Error(`Speak failed (${response.status})`);
  }
  return speakResponseSchema.parse(await response.json());
}

/** Decodes a base64 audio payload (as returned by `speakText`) into a playable object URL. The
 * caller is responsible for `URL.revokeObjectURL` once playback ends/is torn down. */
export function audioBase64ToObjectUrl(audioBase64: string, contentType: string): string {
  const binary = atob(audioBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const blob = new Blob([bytes], { type: contentType });
  return URL.createObjectURL(blob);
}

/** Reads a `Blob` to a base64 string (no `data:...;base64,` prefix) via `FileReader`, since
 * `Buffer` isn't available in the browser. Exported for `lib/api/attachments.ts`'s file/photo
 * attach flow, which needs the exact same encoding — one implementation, not a second copy. */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("Unexpected FileReader result"));
        return;
      }
      const commaIndex = result.indexOf(",");
      resolve(commaIndex === -1 ? result : result.slice(commaIndex + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("FileReader error"));
    reader.readAsDataURL(blob);
  });
}
