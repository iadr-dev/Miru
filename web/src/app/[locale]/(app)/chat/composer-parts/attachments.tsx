"use client";

import { FileIcon, XIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import type { PendingAttachment } from "@/lib/api/attachments";
import { cn } from "@/lib/cn";

/** The inline preview for one staged (not-yet-sent) attachment, with a small "×" remove button —
 * an image shows a thumbnail (plain `<img>`, not `next/image`: the source is either an ephemeral
 * `data:`/blob object URL that was never going to be optimized/served by Next's image pipeline
 * anyway), a long paste shows a scrollable content preview + a "Pasted" badge (`PastedTextPreview`
 * below), and any other document shows a filename chip with a file icon instead, since there's no
 * visual thumbnail to render for a `.docx`/`.pdf`/etc. */
export function AttachmentPreview({
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
