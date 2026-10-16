"use client";

import { useReducedMotion } from "motion/react";
import { useLocale } from "next-intl";
import { type ReactNode, useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompactNumber } from "@/lib/format-number";
import type { TimeBand } from "@/lib/us-macro-chart";
import { formatMacroDay, formatMacroMonth, formatMacroValue } from "@/lib/us-macro-format";
import type { MacroPoint, UnitKind } from "@/types/market/us-macro";

export type MacroLine = {
  readonly id: string;
  readonly label: string;
  /** A categorical `--chart-N` token, never a signed price-move token. */
  readonly color: string;
  readonly points: readonly MacroPoint[];
};

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

const TICK_STYLE = { fill: "var(--text-muted)", fontSize: 11 };

type ChartRow = { t: number } & Record<string, number>;

function isoOf(timestamp: number): string {
  return new Date(timestamp).toISOString().slice(0, 10);
}

function mergeRows(lines: readonly MacroLine[]): ChartRow[] {
  const byTime = new Map<number, ChartRow>();
  for (const line of lines) {
    for (const point of line.points) {
      const t = Date.parse(`${point.date}T00:00:00Z`);
      if (Number.isNaN(t)) {
        continue;
      }
      const row = byTime.get(t) ?? { t };
      row[line.id] = point.value;
      byTime.set(t, row);
    }
  }
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

/** One or two history lines over a real time axis, with optional shaded bands (recession periods)
 * and a zero reference line. Descriptive only: colours are categorical chart tokens. */
export function MacroLineChart({
  lines,
  kind,
  bands,
  showZeroLine,
  ariaLabel,
  height = 260,
}: {
  readonly lines: readonly MacroLine[];
  readonly kind: UnitKind;
  readonly bands: readonly TimeBand[];
  readonly showZeroLine: boolean;
  readonly ariaLabel: string;
  readonly height?: number;
}): ReactNode {
  const locale = useLocale();
  const reduceMotion = useReducedMotion();
  const rows = useMemo(() => mergeRows(lines), [lines]);
  const first = rows[0]?.t;
  const last = rows.at(-1)?.t;
  if (first === undefined || last === undefined) {
    return null;
  }

  return (
    <div role="img" aria-label={ariaLabel} className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[first, last]}
            tick={TICK_STYLE}
            stroke="var(--border)"
            tickFormatter={(value: number) => formatMacroMonth(isoOf(value), locale)}
            minTickGap={36}
          />
          <YAxis
            tick={TICK_STYLE}
            width={48}
            stroke="var(--border)"
            domain={["auto", "auto"]}
            tickFormatter={(value: number) =>
              kind === "percent" ? `${value}%` : formatCompactNumber(value, locale)
            }
          />
          {bands.map((band) => (
            <ReferenceArea
              key={`${band.x1}-${band.x2}`}
              x1={band.x1}
              x2={band.x2}
              fill="var(--chart-6)"
              fillOpacity={0.22}
              stroke="none"
              ifOverflow="hidden"
            />
          ))}
          {showZeroLine ? (
            <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="4 4" />
          ) : null}
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: "var(--text)", fontWeight: 600 }}
            itemStyle={{ color: "var(--text)" }}
            labelFormatter={(value) =>
              typeof value === "number" ? formatMacroDay(isoOf(value), locale) : String(value)
            }
            formatter={(value, name) => [
              typeof value === "number" ? formatMacroValue(value, kind, locale) : String(value),
              String(name),
            ]}
          />
          {lines.map((line) => (
            <Line
              key={line.id}
              dataKey={line.id}
              name={line.label}
              type="monotone"
              stroke={line.color}
              strokeWidth={1.8}
              dot={false}
              connectNulls
              isAnimationActive={!reduceMotion}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
