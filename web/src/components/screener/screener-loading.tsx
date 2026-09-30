"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

/** Shaped shimmer table rows + ghost builder rows — never a bare spinner (`web/CLAUDE.md` §6,
 * "analysis-generation" class since a screener run is a compute pass: narration line + shimmer). */
export function ScreenerLoading(): ReactNode {
  const t = useTranslations("screener");
  return (
    <div className="flex flex-col gap-2 p-4">
      <p className="text-[13px] text-[var(--text-muted)]">{t("loading")}</p>
      {["a", "b", "c", "d", "e"].map((key) => (
        <div
          key={key}
          className="h-10 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]"
        />
      ))}
    </div>
  );
}
