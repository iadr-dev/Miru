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

/** Mirrors `backend/src/config/settings.py`'s `max_attachment_size_bytes` default. A deployment
 * that changes the server setting only loses the client-side early-reject convenience, never
 * correctness — the server's own check is still authoritative. */
export const MAX_ATTACHMENT_SIZE_BYTES = 10_000_000;

/** One attachment staged in the composer before send: the wire payload plus what the UI needs to
 * render a preview/remove row without re-deriving it. `previewUrl` is only set for an image (an
 * object URL for the thumbnail); a document shows its filename/icon instead. */
export type PendingAttachment = {
  id: string;
  filename: string;
  mediaType: string;
  isImage: boolean;
  previewUrl: string | null;
  input: AttachmentInput;
};

/** Converts a `File` to a `PendingAttachment`. Revoke `previewUrl` (via `URL.revokeObjectURL`)
 * when the attachment is removed or sent — the caller owns that lifecycle, mirroring how
 * `composer.tsx` already manages the screenshot capture's own object URLs. */
export async function fileToPendingAttachment(file: File): Promise<PendingAttachment> {
  const dataBase64 = await blobToBase64(file);
  const isImage = IMAGE_MEDIA_TYPES.has(file.type);
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2)}`,
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
    id: `screenshot-${stamp}-${Math.random().toString(36).slice(2)}`,
    filename,
    mediaType: "image/png",
    isImage: true,
    previewUrl: dataUrl,
    input: { filename, media_type: "image/png", data_base64: dataBase64 },
  };
}
