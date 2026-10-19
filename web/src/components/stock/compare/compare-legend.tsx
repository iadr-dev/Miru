"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { dashFor } from "@/components/stock/compare/compare-chart";
import { SignedChange } from "@/components/ui/signed-change";
import { cn } from "@/lib/cn";
import type { CompareSeries } from "@/lib/stock-compare";

/** The legend and the end-of-range figures in one list: each stock is a toggle button (press to
 * hide or show its line) carrying its signed % change over the range. A list under the chart at
 * every width, two columns once the card is wide enough. */
export function CompareLegend({
  series,
  visible,
  colors,
  labels,
  onToggle,
}: {
  readonly series: readonly CompareSeries[];
  readonly visible: ReadonlySet<string>;
  readonly colors: Readonly<Record<string, string>>;
  readonly labels: ReadonlyMap<string, string>;
  readonly onToggle: (symbol: string) => void;
}): ReactNode {
  const t = useTranslations("stock.technical.compare");
  return (
    <div className="mt-3">
      <p className="mb-1 flex flex-wrap justify-between gap-x-3 text-[12px] text-[var(--text-muted)]">
        <span>{t("legendLabel")}</span>
        <span>{t("endHeading")}</span>
      </p>
      <ul className="grid gap-1 @lg:grid-cols-2">
        {series.map((item, index) => {
          const shown = visible.has(item.symbol);
          const stroke = colors[item.symbol] ?? "var(--chart-1)";
          const dash = dashFor(index);
          return (
            <li key={item.symbol}>
              <button
                type="button"
                aria-pressed={shown}
                onClick={() => onToggle(item.symbol)}
                className={cn(
                  "flex min-h-11 w-full items-center gap-3 rounded-[var(--radius-input)] border border-[var(--border)] px-3 text-left text-[13px] transition-colors",
                  "hover:bg-[var(--surface-2)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
                  shown ? "text-[var(--text)]" : "text-[var(--text-muted)] opacity-70",
                )}
              >
                <svg width="28" height="8" role="presentation" aria-hidden className="shrink-0">
                  <line
                    x1="0"
                    y1="4"
                    x2="28"
                    y2="4"
                    stroke={stroke}
                    strokeWidth="2.5"
                    {...(dash ? { strokeDasharray: dash } : {})}
                  />
                </svg>
                <span className={cn("min-w-0 flex-1 truncate", !shown && "line-through")}>
                  <span className="font-num tabular-nums">{item.symbol}</span>
                  <span className="ml-1.5">{labels.get(item.symbol) ?? ""}</span>
                </span>
                {shown ? null : (
                  <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
                    {t("legendHidden")}
                  </span>
                )}
                {item.endPct === null ? (
                  <span className="shrink-0 text-[12px] text-[var(--text-muted)]">
                    {t("noData")}
                  </span>
                ) : (
                  <SignedChange
                    value={item.endPct}
                    decimals={2}
                    unit="%"
                    className="shrink-0 text-[13px]"
                  />
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
