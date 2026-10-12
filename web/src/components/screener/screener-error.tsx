"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

/** Inline calm retry — per `web/OVERVIEW.md` §6 `ErrorState` convention (never a blank page). */
export function ScreenerError({ onRetry }: { readonly onRetry: () => void }): ReactNode {
  const t = useTranslations("screener");
  return (
    <div className="flex flex-col items-center gap-3 p-10 text-center">
      <p className="text-[13px] text-[var(--text-muted)]">{t("error")}</p>
      <button
        type="button"
        onClick={onRetry}
        className="min-h-11 rounded-[var(--radius-pill)] border border-[var(--border)] px-4 text-[13px] text-[var(--text)] hover:bg-[var(--surface-2)]"
      >
        {t("retry")}
      </button>
    </div>
  );
}
