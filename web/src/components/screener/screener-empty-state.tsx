"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

/** "query/coverage" class empty state (interaction-spec §4) — inline, no chat CTA, offers
 * "放寬 gates" — distinct from `stock-empty-state.tsx`'s cold-start class. */
export function ScreenerEmptyState({
  onRelaxGates,
}: {
  readonly onRelaxGates: () => void;
}): ReactNode {
  const t = useTranslations("screener");
  return (
    <div className="flex flex-col items-center gap-3 p-10 text-center">
      <p className="text-[var(--text-muted)] text-sm">{t("emptyState")}</p>
      <button
        type="button"
        onClick={onRelaxGates}
        className="min-h-11 rounded-[var(--radius-pill)] border border-[var(--accent)] px-4 text-[13px] text-[var(--accent)] hover:bg-[var(--accent-weak)]"
      >
        {t("relaxGates")}
      </button>
    </div>
  );
}
