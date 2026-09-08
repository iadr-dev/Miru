"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import type { SectorRotationDayRow } from "@/types/market";

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

// Only the top N industries by their most-recent-day rank get their own line + color — plotting
// every industry (often 40+) on one bump chart is unreadable; this mirrors `get_index_contribution`'s
// own top-N-not-whole-market reasoning for a ranked visual, not a data-completeness cut (the
// data-table fallback below still lists every industry actually present).
const MAX_PLOTTED_INDUSTRIES = 8;

interface BumpChartPoint {
  trade_date: string;
  [industry: string]: string | number | null;
}

export function pivotByDate(
  rows: readonly SectorRotationDayRow[],
  tradeDates: readonly string[],
  industries: readonly string[],
): BumpChartPoint[] {
  const byDate = new Map<string, Map<string, number>>();
  for (const row of rows) {
    const perDate = byDate.get(row.trade_date) ?? new Map<string, number>();
    perDate.set(row.industry, row.rank);
    byDate.set(row.trade_date, perDate);
  }
  return tradeDates.map((date) => {
    const perDate = byDate.get(date);
    const point: BumpChartPoint = { trade_date: date };
    for (const industry of industries) {
      point[industry] = perDate?.get(industry) ?? null;
    }
    return point;
  });
}

/**
 * 板塊輪動 rank-bump chart — one line per industry, Y = that day's rank by net institutional
 * flow (1 = most net inflow that day), X = the last N trading days. Deliberately not an RRG:
 * "rank" is a neutral, non-signed, non-trading-language term (unlike RRG's "leading"/"lagging"
 * quadrants), and needs no benchmark index to compute — see `get_sector_rotation`'s own
 * docstring for the design reasoning this component follows.
 *
 * Colors come from `--chart-1..6` (categorical, never `--up`/`--down`) since a rank isn't a
 * signed price/% figure. Every industry actually present is listed in the data-table fallback
 * even when only the top `MAX_PLOTTED_INDUSTRIES` get their own chart line — a screen-reader or
 * keyboard-only user gets the complete picture from the table, never a chart-only summary.
 */
export function SectorRotationBumpChart({
  rows,
  tradeDates,
}: {
  readonly rows: readonly SectorRotationDayRow[];
  readonly tradeDates: readonly string[];
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");
  const [highlighted, setHighlighted] = useState<string | null>(null);

  const latestDate = tradeDates.at(-1);
  const allIndustries = useMemo(() => {
    const byIndustry = new Map<string, number>();
    for (const row of rows) {
      if (row.trade_date === latestDate) byIndustry.set(row.industry, row.rank);
    }
    return [...byIndustry.entries()].sort((a, b) => a[1] - b[1]).map(([industry]) => industry);
  }, [rows, latestDate]);
  const plottedIndustries = allIndustries.slice(0, MAX_PLOTTED_INDUSTRIES);

  const points = useMemo(
    () => pivotByDate(rows, tradeDates, plottedIndustries),
    [rows, tradeDates, plottedIndustries],
  );

  const maxRank = allIndustries.length;

  if (rows.length === 0 || tradeDates.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("rotationUnavailable")}</p>;
  }

  return (
    <div>
      <div style={{ width: "100%", height: 320 }}>
        <ResponsiveContainer>
          <LineChart data={points} margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              stroke="var(--border)"
            />
            <YAxis
              reversed
              allowDecimals={false}
              domain={[1, Math.max(maxRank, 1)]}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              width={28}
              stroke="var(--border)"
              label={{
                value: t("rankAxisLabel"),
                angle: -90,
                position: "insideLeft",
                fill: "var(--text-muted)",
                fontSize: 11,
              }}
            />
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              formatter={(value, name) => [
                t("rankTooltip", { rank: formatNumber(value as number, locale) }),
                name,
              ]}
            />
            {plottedIndustries.map((industry, index) => {
              const isHighlighted = highlighted === industry;
              const isDimmed = highlighted !== null && !isHighlighted;
              return (
                <Line
                  key={industry}
                  dataKey={industry}
                  name={industry}
                  type="monotone"
                  stroke={CHART_COLORS[index % CHART_COLORS.length]}
                  strokeWidth={isHighlighted ? 3 : 2}
                  strokeOpacity={isDimmed ? 0.25 : 1}
                  dot={{ r: 3 }}
                  connectNulls
                  isAnimationActive={false}
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div role="group" aria-label={t("legendLabel")} className="mt-2 flex flex-wrap gap-2">
        {plottedIndustries.map((industry, index) => (
          <button
            key={industry}
            type="button"
            onMouseEnter={() => setHighlighted(industry)}
            onMouseLeave={() => setHighlighted(null)}
            onFocus={() => setHighlighted(industry)}
            onBlur={() => setHighlighted(null)}
            className={cn(
              "flex items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px]",
              "bg-[var(--surface-2)] text-[var(--text)] transition-opacity",
              "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
              highlighted !== null && highlighted !== industry ? "opacity-50" : null,
            )}
          >
            <span
              aria-hidden
              className="size-2.5 rounded-full"
              style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }}
            />
            {industry}
          </button>
        ))}
      </div>

      {/* Screen-reader / keyboard fallback: every industry actually present, not just the
       * `MAX_PLOTTED_INDUSTRIES` that got a chart line — a table conveys the same rank-over-time
       * data the chart does, without depending on hover/color perception. */}
      <div className="mt-3 overflow-x-auto">
        <table className="min-w-full text-[12px]">
          <caption className="sr-only">{t("tableCaption")}</caption>
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1 text-left text-[var(--text-muted)]">
                {t("columnIndustry")}
              </th>
              {tradeDates.map((date) => (
                <th
                  key={date}
                  scope="col"
                  className="px-2 py-1 text-right font-num text-[var(--text-muted)] tabular-nums"
                >
                  {date}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {allIndustries.map((industry) => {
              const byDate = new Map(
                rows.filter((r) => r.industry === industry).map((r) => [r.trade_date, r.rank]),
              );
              return (
                <tr key={industry} className="border-[var(--border)] border-t">
                  <th scope="row" className="px-2 py-1 text-left font-normal text-[var(--text)]">
                    {industry}
                  </th>
                  {tradeDates.map((date) => (
                    <td
                      key={date}
                      className="px-2 py-1 text-right font-num text-[var(--text)] tabular-nums"
                    >
                      {byDate.get(date) ?? "—"}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
