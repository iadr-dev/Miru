"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SignedChange } from "@/components/ui/signed-change";
import { cn } from "@/lib/cn";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionOf } from "@/lib/signed";
import type { SectorHeatmapRow } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[1.4fr_0.9fr_0.9fr_0.9fr_1fr]";

/**
 * The 產業熱力圖 expand-modal's data-detail list — `get_sector_heatmap`'s rows, already ranked by
 * `avg_pct_change` descending. Same "expand to a data TABLE, never a re-rendered chart"
 * convention every other market card's `ExpandModal` in this dashboard follows (e.g.
 * `SectorConstituentsTable`, `StockHeatmapTable`) — a treemap laid out for 45+ industries inside
 * a modal-sized box re-creates the exact illegible-tiny-tile problem the collapsed inline view
 * was built to avoid.
 */
export function SectorHeatmapTable({
  rows,
}: {
  readonly rows: readonly SectorHeatmapRow[];
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const locale = useLocale();

  if (rows.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>;
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
          <span>{t("columnIndustry")}</span>
          <span className="text-right">{t("columnChange")}</span>
          <span className="text-right">{t("columnStockCount")}</span>
          <span className="text-right">{t("columnVolume")}</span>
          <span className="text-right">{t("columnTurnover")}</span>
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) => {
            const direction = directionOf(row.avg_pct_change, 2);
            return (
              <li
                key={row.industry}
                role="group"
                aria-label={`${row.industry} ${t(direction)} ${formatNumber(Math.abs(row.avg_pct_change), locale, { decimals: 2 })}%`}
                className={cn("grid items-center gap-2.5 py-2", ROW_GRID_COLS)}
              >
                <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
                  {row.industry}
                </span>
                <SignedChange
                  value={row.avg_pct_change}
                  decimals={2}
                  unit="%"
                  spaced={false}
                  locale={locale}
                  labelled={false}
                  className="text-right text-[12px]"
                />
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums"
                >
                  {t("stockCount", { count: row.stock_count })}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums"
                >
                  {formatCompactNumber(row.total_volume, locale)}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums"
                >
                  {row.total_turnover !== null
                    ? formatCompactNumber(row.total_turnover, locale)
                    : "—"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
