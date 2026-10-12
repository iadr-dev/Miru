"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SignedChange } from "@/components/ui/signed-change";
import { cn } from "@/lib/cn";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionOf } from "@/lib/signed";
import type { StockHeatmapRow } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[1.4fr_1fr_0.9fr_0.9fr_0.9fr_1fr]";

/**
 * The 股票熱力圖 expand-modal's data-detail list — `get_stock_heatmap`'s rows, already ranked by
 * `size_by` descending. Same "expand to a data TABLE, never a re-rendered chart" convention
 * every other market card in this dashboard follows for its `ExpandModal` (e.g.
 * `SectorConstituentsTable`) — a treemap laid out for 150 stocks inside a modal-sized box just
 * re-creates the same illegible-tiny-tile problem the collapsed inline view was built to avoid.
 */
export function StockHeatmapTable({
  rows,
}: {
  readonly rows: readonly StockHeatmapRow[];
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const locale = useLocale();

  if (rows.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("constituentsEmpty")}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[600px]">
        <div
          aria-hidden
          className={cn(
            "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
            ROW_GRID_COLS,
          )}
        >
          <span>{t("columnStock")}</span>
          <span>{t("columnIndustry")}</span>
          <span className="text-right">{t("columnClose")}</span>
          <span className="text-right">{t("columnChange")}</span>
          <span className="text-right">{t("columnVolume")}</span>
          <span className="text-right">{t("columnTurnover")}</span>
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) => {
            const direction = directionOf(row.pct_change, 2);
            return (
              <li
                key={row.symbol}
                role="group"
                aria-label={`${row.stock_name} ${row.symbol} ${t(direction)} ${formatNumber(Math.abs(row.pct_change), locale, { decimals: 2 })}%`}
                className={cn("grid items-center gap-2.5 py-2", ROW_GRID_COLS)}
              >
                <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
                  {row.stock_name}
                  <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
                </span>
                <span aria-hidden className="truncate text-[12px] text-[var(--text-muted)]">
                  {row.industry}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[12px] text-[var(--text)] tabular-nums"
                >
                  {formatNumber(row.close, locale, { decimals: 1 })}
                </span>
                <SignedChange
                  value={row.pct_change}
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
