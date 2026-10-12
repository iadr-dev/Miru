"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import type { FunnelCounts } from "@/types/screener";

/** A compact rail-bottom summary of the SAME funnel counts `FunnelMeter` already shows at the
 * top of the page — a quick "how selective is this screen" readout for when the builder rail is
 * the only part still in view (README.md reference: AlphaScreener's "Funnel Match Rate" card).
 * Deliberately just `setup/universe` from the real funnel response — no synthetic win-rate/Sharpe
 * stat, since this pass's factor catalog has no backtest data to back one. Callers only render
 * this when `isFunnelUniverseKnown` — US has no real universe count to divide by, so the card is
 * omitted there rather than showing a placeholder for a number that can't exist. */
export function FunnelMatchRate({
  funnel,
}: {
  readonly funnel: FunnelCounts | undefined;
}): ReactNode {
  const t = useTranslations("screener.funnel");
  const universe = funnel?.universe ?? 0;
  const setup = funnel?.setup ?? 0;
  const rate = universe > 0 ? Math.round((setup / universe) * 100) : 0;

  return (
    <div className="surface-card flex flex-col gap-1.5 p-3">
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-[var(--text-muted)]">{t("matchRate")}</span>
        <span aria-live="off" className="font-num text-[13px] text-[var(--accent)] tabular-nums">
          {rate}%
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-[var(--radius-pill)] bg-[var(--surface-2)]">
        <div
          className="h-full bg-[var(--accent)] transition-[width]"
          style={{ width: `${rate}%` }}
        />
      </div>
      <span aria-live="off" className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
        {t("matchRateDetail", { setup, universe })}
      </span>
    </div>
  );
}
