"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { type FunnelCounts, isFunnelUniverseKnown } from "@/types/screener";

function pct(count: number, total: number): number {
  return total > 0 ? Math.round((count / total) * 100) : 0;
}

/** Three funnel stages as labeled, colored pills (universe → gated → matched) with a thin
 * proportional progress bar beneath — README.md §1's funnel, made legible: each stage names
 * itself, its count, and its share of the universe, rather than a bare "0→0→0" tucked in a
 * corner. `aria-live="off"` — this churns on every debounced condition/gate edit, which is
 * exactly the "churn" class `web/CLAUDE.md` §7 says stays silent for screen readers. */
export function FunnelMeter({ funnel }: { readonly funnel: FunnelCounts | undefined }): ReactNode {
  const t = useTranslations("screener.funnel");
  const universe = funnel?.universe ?? 0;
  const gated = funnel?.gated ?? 0;
  const setup = funnel?.setup ?? 0;
  const universeKnown = isFunnelUniverseKnown(funnel);
  const gatedPct = pct(gated, universe);
  const setupPct = pct(setup, universe);

  return (
    <div className="flex flex-col gap-2 border-[var(--border)] border-b bg-[var(--surface)] px-4 py-3">
      <div
        aria-live="off"
        className="flex flex-wrap items-center gap-2 font-num text-[13px] tabular-nums"
      >
        <span className="inline-flex items-center gap-1.5 text-[var(--text-muted)]">
          <span className="size-1.5 rounded-full bg-[var(--text-muted)]" />
          {t("universe")} {universeKnown ? `${universe.toLocaleString()} 檔` : "—"}
        </span>
        {universeKnown ? (
          <>
            <span className="text-[var(--text-muted)]">→</span>
            <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-[color-mix(in_srgb,var(--chart-2)_16%,transparent)] px-2.5 py-1 text-[var(--chart-2)]">
              <span className="size-1.5 rounded-full bg-[var(--chart-2)]" />
              {t("gated")} {gated.toLocaleString()} 檔 ({gatedPct}%)
            </span>
          </>
        ) : null}
        <span className="text-[var(--text-muted)]">→</span>
        <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-[var(--accent-weak)] px-2.5 py-1 font-semibold text-[var(--accent)]">
          <span className="size-1.5 rounded-full bg-[var(--accent)]" />
          {t("setup")} {setup.toLocaleString()} 檔
        </span>
      </div>
      {universeKnown ? (
        <div className="relative h-1.5 overflow-hidden rounded-[var(--radius-pill)] bg-[var(--surface-2)]">
          <div
            className="absolute inset-y-0 left-0 bg-[color-mix(in_srgb,var(--chart-2)_45%,transparent)] transition-[width]"
            style={{ width: `${gatedPct}%` }}
          />
          <div
            className="absolute inset-y-0 left-0 bg-[var(--accent)] transition-[width]"
            style={{ width: `${setupPct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
