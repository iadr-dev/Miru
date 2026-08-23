"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";

/**
 * A single big tabular stat with a 紅漲綠跌 direction indicator — generic over any signed metric
 * (margin-maintenance ratio, a day-trading volume delta, …). `priorValue`, if given, drives the
 * ▲/▼ + `--up`/`--down` computation; direction is never color-alone — the `aria-label` always
 * carries both the arrow word and the value (web/CLAUDE.md §4).
 */
export function StatCard({
  label,
  value,
  priorValue,
  unit,
  decimals,
}: {
  label: string;
  value: number | null;
  priorValue?: number | null;
  unit?: string;
  decimals?: number;
}): ReactNode {
  const locale = useLocale();
  const delta = priorValue != null && value != null ? value - priorValue : null;
  const isUp = delta != null ? delta > 0 : null;
  const isDown = delta != null ? delta < 0 : null;

  const directionWord = isUp ? "上漲" : isDown ? "下跌" : null;
  const formattedValue = formatNumber(value, locale, decimals != null ? { decimals } : undefined);
  const formattedDelta =
    delta != null ? formatNumber(Math.abs(delta), locale, { decimals: decimals ?? 2 }) : null;

  return (
    <div className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-3">
      <p className="text-[12px] text-[var(--text-muted)]">{label}</p>
      <div
        role="group"
        aria-label={
          directionWord && formattedDelta
            ? `${label} ${directionWord} ${formattedDelta}${unit ?? ""}`
            : `${label} ${formattedValue}${unit ?? ""}`
        }
        className="mt-1 font-num text-[22px] text-[var(--text)] tabular-nums"
      >
        {formattedValue}
        {unit ?? ""}
      </div>
      {delta != null ? (
        <p
          aria-hidden
          className={cn(
            "mt-0.5 flex items-center gap-1 font-num text-[12px] tabular-nums",
            isUp && "text-[var(--up)]",
            isDown && "text-[var(--down)]",
            !isUp && !isDown && "text-[var(--text-muted)]",
          )}
        >
          <span>{isUp ? "▲" : isDown ? "▼" : "–"}</span>
          <span>
            {formattedDelta}
            {unit ?? ""}
          </span>
        </p>
      ) : null}
    </div>
  );
}
