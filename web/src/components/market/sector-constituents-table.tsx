"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import type { SectorConstituentRow } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[1.4fr_0.9fr_0.9fr_0.9fr_1fr]";

/**
 * Per-stock drill-down table for one `SectorHeatmapTreemap` tile — `get_sector_constituents`'s
 * rows, already ranked `pct_change` descending by the backend. Shared verbatim between the
 * card's inline (sliced) preview and the `ExpandModal` full list, same convention every other
 * market table in this dashboard follows.
 */
export function SectorConstituentsTable({
  rows,
}: {
  readonly rows: readonly SectorConstituentRow[];
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const locale = useLocale();

  if (rows.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("constituentsEmpty")}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[520px]">
        <div
          aria-hidden
          className={cn(
            "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
            ROW_GRID_COLS,
          )}
        >
          <span>{t("columnStock")}</span>
          <span className="text-right">{t("columnClose")}</span>
          <span className="text-right">{t("columnChange")}</span>
          <span className="text-right">{t("columnVolume")}</span>
          <span className="text-right">{t("columnTurnover")}</span>
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) => {
            const up = row.pct_change >= 0;
            const arrow = up ? "▲" : "▼";
            const sign = up && row.pct_change > 0 ? "+" : "";
            const color = up ? "var(--up)" : "var(--down)";
            return (
              <li
                key={row.symbol}
                role="group"
                aria-label={`${row.stock_name} ${row.symbol} ${up ? t("up") : t("down")} ${formatNumber(Math.abs(row.pct_change), locale, { decimals: 2 })}%`}
                className={cn("grid items-center gap-2.5 py-2", ROW_GRID_COLS)}
              >
                <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
                  {row.stock_name}
                  <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text)] tabular-nums"
                >
                  {formatNumber(row.close, locale, { decimals: 1 })}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] tabular-nums"
                  style={{ color }}
                >
                  {arrow}
                  {sign}
                  {formatNumber(row.pct_change, locale, { decimals: 2 })}%
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums"
                >
                  {formatCompactNumber(row.volume, locale)}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums"
                >
                  {row.turnover !== null ? formatCompactNumber(row.turnover, locale) : "—"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
