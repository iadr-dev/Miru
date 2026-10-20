"use client";

import { WarningIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Plain-data loading skeleton for a Settings sub-page (no narration —
 * config forms are never the analysis-generation loading class). */
export function SettingsLoading({ rows = 3 }: Readonly<{ rows?: number }>): ReactNode {
  const keys = Array.from({ length: rows }, (_, i) => `skeleton-row-${i}`);
  return (
    <div className="animate-pulse space-y-3" aria-hidden>
      {keys.map((key) => (
        <div key={key} className="h-14 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      ))}
    </div>
  );
}

/** Calm inline retry state — a per-panel failure never blanks the page. */
export function SettingsError({
  message,
  onRetry,
}: Readonly<{
  message: string;
  onRetry?: () => void;
}>): ReactNode {
  const t = useTranslations("settings.common");
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-[var(--radius-input)] border border-[var(--warn)]/40",
        "bg-[var(--surface-2)] p-4 text-sm",
      )}
    >
      <WarningIcon size={18} weight="regular" className="mt-0.5 shrink-0 text-[var(--warn)]" />
      <div className="min-w-0 flex-1">
        <p className="text-[var(--text)]">{message}</p>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 font-medium text-[var(--accent-text)] text-sm hover:underline"
          >
            {t("retry")}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** Transient "已儲存"-style success toast (save flow). Purely local/visual —
 * callers mount it conditionally and clear it on a timer. */
export function SavedToast({ message }: Readonly<{ message: string }>): ReactNode {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-6 left-1/2 z-40 -translate-x-1/2 rounded-[var(--radius-pill)]",
        "border border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-[var(--text)] text-sm shadow-lg",
      )}
    >
      {message}
    </div>
  );
}
