"use client";

import { WarningIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** Plain-data loading skeleton for a Settings sub-page (interaction-spec §2.13: no narration —
 * config forms are never the analysis-generation loading class). */
export function SettingsLoading({ rows = 3 }: { rows?: number }): ReactNode {
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
}: {
  message: string;
  onRetry?: () => void;
}): ReactNode {
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

/** Transient "已儲存"-style success toast (interaction-spec §2.13 save flow). Purely local/visual —
 * callers mount it conditionally and clear it on a timer. */
export function SavedToast({ message }: { message: string }): ReactNode {
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

/** Calm "requires Pro" gate message for the 403 `feature_not_entitled` path — plain copy, no 馬賽克
 * teaser (interaction-spec §2.13: 馬賽克 never appears in Settings). */
export function RequiresProNotice({ onUpgrade }: { onUpgrade: () => void }): ReactNode {
  const t = useTranslations("settings.common");
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center">
      <p className="text-[var(--text)] text-sm">{t("requiresPro")}</p>
      <Button variant="outline" size="sm" className="mt-3" onClick={onUpgrade}>
        {t("goToBilling")}
      </Button>
    </div>
  );
}
