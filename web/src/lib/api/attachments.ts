/**
 * Chat-attachment client helpers: turning a picked `File` (or a captured screenshot) into the
 * `AttachmentInput` wire shape `/agent/query`/`edit` accept, plus the same caps the backend
 * enforces (`backend/src/api/attachments.py`) mirrored here for immediate client-side feedback —
 * the backend remains the actual enforcement boundary; this is UX, not the guard.
 */
import type { AttachmentInput } from "@/lib/api/agent-stream";
import { blobToBase64 } from "@/lib/api/voice";

/** Mirrors `backend/src/attachments/processing.py::IMAGE_MEDIA_TYPES` — anything else is routed
 * through `markitdown` as a document on the backend. */
export const IMAGE_MEDIA_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

/** Document types `markitdown` (the backend's extractor) actually converts — mirrors the
 * `attachments = ["markitdown[docx,pptx,xlsx,pdf]"]` extra in `backend/pyproject.toml`. Plain
 * text/CSV/Markdown are included too since `markitdown` passes them through as-is. */
export const DOCUMENT_MEDIA_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/plain",
  "text/csv",
  "text/markdown",
]);

/** The `accept` attribute for the file picker's `<input type="file">` — every media type the
 * backend can actually process, so the OS-native picker filters out anything the turn would
 * otherwise reject with `attachment_extraction_failed`. */
export const ACCEPTED_ATTACHMENT_MEDIA_TYPES = [...IMAGE_MEDIA_TYPES, ...DOCUMENT_MEDIA_TYPES].join(
  ",",
);

/** Mirrors `backend/src/api/attachments.py`'s `_MAX_ATTACHMENTS_PER_REQUEST`. */
export const MAX_ATTACHMENTS_PER_TURN = 10;

/** One attachment staged in the composer before send: the wire payload plus what the UI needs to
 * render a preview/remove row without re-deriving it. `previewUrl` is only set for an image (an
 * object URL for the thumbnail); a document shows its filename/icon instead; `pastedText` is only
 * set for a long-paste-turned-attachment (see `shouldAttachPastedText` below), whose card shows a
 * truncated content preview + a "pasted" badge instead of either. */
export type PendingAttachment = {
  id: string;
  filename: string;
  mediaType: string;
  isImage: boolean;
  previewUrl: string | null;
  input: AttachmentInput;
  pastedText?: string;
};

/** Above this length OR line count, a paste is treated as "pasted content" rather than inserted
 * into the draft — mirrors the Claude.ai/ChatGPT convention of keeping the compose box from being
 * flooded by a large block of text, instead surfacing it as a removable attachment chip. Whichever
 * threshold trips first wins (a long single-line paste and a many-short-lines paste are both
 * "too much text for the box," even though only one of the two counts is large). */
export const PASTE_TO_ATTACHMENT_CHAR_THRESHOLD = 1500;
export const PASTE_TO_ATTACHMENT_LINE_THRESHOLD = 20;

export function shouldAttachPastedText(text: string): boolean {
  if (text.length > PASTE_TO_ATTACHMENT_CHAR_THRESHOLD) {
    return true;
  }
  return text.split("\n").length > PASTE_TO_ATTACHMENT_LINE_THRESHOLD;
}

/** Converts a long clipboard paste into a `PendingAttachment` carrying the raw text as a
 * `text/plain` attachment (the backend's `markitdown` extractor passes plain text through as-is —
 * see `DOCUMENT_MEDIA_TYPES` above) — same wire shape as a picked `.txt` file, just sourced from
 * the clipboard instead of the file picker. `pastedText` keeps the original string for the
 * composer's own truncated-preview card; it never round-trips through `data_base64` for display. */
export async function pastedTextToPendingAttachment(text: string): Promise<PendingAttachment> {
  const blob = new Blob([text], { type: "text/plain" });
  const dataBase64 = await blobToBase64(blob);
  const stamp = Date.now();
  const filename = `pasted-text-${stamp}.txt`;
  return {
    id: `pasted-${stamp}-${crypto.randomUUID()}`,
    filename,
    mediaType: "text/plain",
    isImage: false,
    previewUrl: null,
    input: { filename, media_type: "text/plain", data_base64: dataBase64 },
    pastedText: text,
  };
}

/** Converts a `File` to a `PendingAttachment`. Revoke `previewUrl` (via `URL.revokeObjectURL`)
 * when the attachment is removed or sent — the caller owns that lifecycle, mirroring how
 * `composer.tsx` already manages the screenshot capture's own object URLs. */
export async function fileToPendingAttachment(file: File): Promise<PendingAttachment> {
  const dataBase64 = await blobToBase64(file);
  const isImage = IMAGE_MEDIA_TYPES.has(file.type);
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID()}`,
    filename: file.name,
    mediaType: file.type,
    isImage,
    previewUrl: isImage ? URL.createObjectURL(file) : null,
    input: { filename: file.name, media_type: file.type, data_base64: dataBase64 },
  };
}

/** Converts a captured screenshot (`data:image/png;base64,...`) into the same `PendingAttachment`
 * shape a picked file produces, so the composer's attachment list/send path has exactly one
 * representation regardless of source. */
export function screenshotToPendingAttachment(dataUrl: string): PendingAttachment {
  const commaIndex = dataUrl.indexOf(",");
  const dataBase64 = commaIndex === -1 ? dataUrl : dataUrl.slice(commaIndex + 1);
  const stamp = Date.now();
  const filename = `screenshot-${stamp}.png`;
  return {
    id: `screenshot-${stamp}-${crypto.randomUUID()}`,
    filename,
    mediaType: "image/png",
    isImage: true,
    previewUrl: dataUrl,
    input: { filename, media_type: "image/png", data_base64: dataBase64 },
  };
}
