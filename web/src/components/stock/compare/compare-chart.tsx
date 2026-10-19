"use client";

import { useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TOOLTIP_STYLE } from "@/components/stock/chip/shared";
import { formatNumber } from "@/lib/format-number";
import { formatSignedChange } from "@/lib/signed";
import type { CompareRange, RebasedRow } from "@/lib/stock-compare";

/** Stroke patterns by series index, so the lines stay tellable apart without relying on hue
 * alone (the first is solid). */
export const COMPARE_DASHES: readonly (string | undefined)[] = [
  undefined,
  "7 4",
  "2 3",
  "10 3 2 3",
  "1 5",
];

export function dashFor(index: number): string | undefined {
  return COMPARE_DASHES[index % COMPARE_DASHES.length];
}

/** MM-DD up to six months, YY/MM across a year (where MM-DD alone would repeat). */
function tickLabel(date: string, range: CompareRange): string {
  return range === "1y" ? `${date.slice(2, 4)}/${date.slice(5, 7)}` : date.slice(5);
}

/** One line per visible symbol over the rebased rows. Series colours are the categorical
 * `--chart-*` tokens, never `--up`/`--down`; the zero line marks the base date. */
export function CompareChart({
  rows,
  symbols,
  visible,
  colors,
  labels,
  range,
  ariaLabel,
}: {
  readonly rows: readonly RebasedRow[];
  readonly symbols: readonly string[];
  readonly visible: ReadonlySet<string>;
  readonly colors: Readonly<Record<string, string>>;
  readonly labels: ReadonlyMap<string, string>;
  readonly range: CompareRange;
  readonly ariaLabel: string;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("stock.technical.compare");
  const reduceMotion = useReducedMotion();
  const data = useMemo(() => rows.map((row) => ({ date: row.date, ...row.pct })), [rows]);

  return (
    <div role="img" aria-label={ariaLabel} className="h-[260px] w-full @lg:h-[340px]">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={(value: string) => tickLabel(value, range)}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            minTickGap={36}
          />
          <YAxis
            tickFormatter={(value: number) =>
              `${value > 0 ? "+" : ""}${formatNumber(value, locale, { decimals: 0 })}%`
            }
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={52}
          />
          <ReferenceLine y={0} stroke="var(--text-muted)" strokeOpacity={0.5} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            itemStyle={{ color: "var(--text)" }}
            labelStyle={{ color: "var(--text-muted)" }}
            formatter={(value, name) => [
              typeof value === "number"
                ? formatSignedChange(value, locale, { decimals: 2, unit: "%" })
                : t("noData"),
              String(name),
            ]}
          />
          {symbols.map((symbol, index) => {
            const dash = dashFor(index);
            return visible.has(symbol) ? (
              <Line
                key={symbol}
                type="monotone"
                dataKey={symbol}
                name={`${symbol} ${labels.get(symbol) ?? ""}`.trim()}
                stroke={colors[symbol] ?? "var(--chart-1)"}
                strokeWidth={2}
                {...(dash ? { strokeDasharray: dash } : {})}
                dot={false}
                connectNulls
                isAnimationActive={!reduceMotion}
              />
            ) : null;
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
