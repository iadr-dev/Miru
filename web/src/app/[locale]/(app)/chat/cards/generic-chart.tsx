"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { distinctChartColors, hashToChartToken } from "@/lib/chart-token-hash";
import { fieldLabel, valueLabel } from "@/lib/field-labels";
import { asArray, asNumber, formatCompactNumber, formatNumber } from "@/lib/format-number";
import { type ChartHint, chartHintSchema, type Mark } from "@/types/chart-hint";

/** `arc` (pie/donut) degrades past this many distinct categories — an N-slice pie beyond this is
 * an illegible sliver-chart (Vega-Lite-pattern research: the standard pie-misuse cardinality
 * limit), so the renderer buckets the remainder into one "其他/Other" slice rather than drawing
 * every slice. */
const MAX_ARC_CATEGORIES = 8;
/** Multi-series `bar`/`line`/`area`/`point` are capped at the 10 available `--chart-N` tokens
 * (`tokens.css`) — past this, additional series are dropped (never wrapped/reused, which would
 * silently make two different categories look like the same series). */
const MAX_CHART_TOKENS = 10;

/** Resolves the render color for one categorical series label — `hashToChartToken` so the same
 * label always renders the same color across renders/reorderings (fixes the index-based-coloring
 * inconsistency in `BarChart`/`GroupedBarChart`/`TimeseriesChart`, which reassigns colors whenever
 * series order shifts call-to-call). `diverging=true` instead resolves from the signed `--up`/
 * `--down` tokens by `value`'s sign — never a `--chart-N` token for signed data (the
 * 紅漲綠跌 rule). Exported for unit testing (no rendered-DOM test convention exists in
 * this repo — see `sector-heatmap-treemap.test.ts`). */
export function resolveSeriesColor(
  label: string,
  diverging: boolean,
  value: number | null,
): string {
  if (diverging) {
    return (value ?? 0) >= 0 ? "var(--up)" : "var(--down)";
  }
  return hashToChartToken(label);
}

/** Whether `distinctCount` categorical values fit `mark`'s renderability cap — `arc` degrades at
 * `MAX_ARC_CATEGORIES`, other multi-series marks at `MAX_CHART_TOKENS`. Returns the cap actually
 * applied so the caller can bucket/truncate to exactly that count. */
export function checkCardinality(
  distinctCount: number,
  mark: Mark,
): { fits: boolean; limit: number } {
  const limit = mark === "arc" ? MAX_ARC_CATEGORIES : MAX_CHART_TOKENS;
  return { fits: distinctCount <= limit, limit };
}

/** A category/series label can legitimately be a `string` (e.g. `investor` names) or a `number`
 * (e.g. `DividendYearSummary.year`) in this app's row data — `asString` (from `card-format.ts`)
 * only accepts real strings, so a numeric category field would silently fail every row here
 * without this coercion. Never accepts `boolean`/`object`/`null` — those aren't valid category
 * labels either. */
function asCategoryLabel(value: unknown): string | null {
  if (typeof value === "string" && value.length > 0) {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

type ArcSlice = { label: string; value: number };

/** Aggregates `rows` by `colorField`'s value, summing `thetaField` per group — the shape a pie
 * needs (one slice per distinct category). Categories beyond `checkCardinality`'s `arc` limit are
 * folded into one trailing "other" slice (`otherLabel`) rather than silently dropped or rendered
 * as an illegible sliver-pie. */
export function groupArcSlices(
  rows: Record<string, unknown>[],
  colorField: string,
  thetaField: string,
  otherLabel: string,
): ArcSlice[] {
  const totals = new Map<string, number>();
  for (const row of rows) {
    const label = asCategoryLabel(row[colorField]);
    const value = asNumber(row[thetaField]);
    if (label === null || value === null) {
      continue;
    }
    totals.set(label, (totals.get(label) ?? 0) + value);
  }
  const slices = [...totals.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
  const { fits, limit } = checkCardinality(slices.length, "arc");
  if (fits) {
    return slices;
  }
  const kept = slices.slice(0, limit - 1);
  const otherTotal = slices.slice(limit - 1).reduce((sum, s) => sum + s.value, 0);
  return [...kept, { label: otherLabel, value: otherTotal }];
}

type SeriesPoint = { x: string; [seriesLabel: string]: string | number | null };

/** Pivots long-format rows (one row per `x`/`colorField` pair) into the wide, one-column-per-
 * series shape `recharts`' `<Bar>`/`<Line>`/`<Area>`/`<Scatter>` each need — mirrors what every
 * existing chart card does implicitly for its own fixed field set, generalized over an arbitrary
 * `colorField`. Also returns the distinct series labels actually kept (post-cardinality-cap, in
 * first-seen order) so the caller renders exactly one recharts series element per label. */
export function pivotByColor(
  rows: Record<string, unknown>[],
  xField: string,
  yField: string,
  colorField: string | null,
): { points: SeriesPoint[]; seriesLabels: string[] } {
  if (colorField === null) {
    const points = rows.map((row) => ({
      x: asCategoryLabel(row[xField]) ?? "",
      value: asNumber(row[yField]),
    }));
    return { points, seriesLabels: ["value"] };
  }
  const byX = new Map<string, SeriesPoint>();
  const seenLabels: string[] = [];
  const seenSet = new Set<string>();
  for (const row of rows) {
    const x = asCategoryLabel(row[xField]) ?? "";
    const label = asCategoryLabel(row[colorField]);
    const value = asNumber(row[yField]);
    if (label === null || value === null) {
      continue;
    }
    if (!seenSet.has(label)) {
      seenSet.add(label);
      seenLabels.push(label);
    }
    const point = byX.get(x) ?? { x };
    point[label] = value;
    byX.set(x, point);
  }
  const { fits, limit } = checkCardinality(seenLabels.length, "bar");
  const keptLabels = fits ? seenLabels : seenLabels.slice(0, limit);
  return { points: [...byX.values()], seriesLabels: keptLabels };
}

/** `dataKey` (from `hint.data_key`) disambiguates which of a tool's own array fields to plot —
 * required whenever an `output_model` bundles more than one row-list (e.g. `events` +
 * `year_summaries` + `fill_results`), where "the first array field found" would otherwise be
 * ambiguous. `null` falls back to the single-row-list convention every existing chart card uses
 * (`record.rows`, else the first array-valued field). */
function extractRows(data: unknown, dataKey: string | null): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  if (dataKey !== null) {
    return Array.isArray(record[dataKey]) ? asArray(record[dataKey]) : [];
  }
  if (Array.isArray(record.rows)) {
    return asArray(record.rows);
  }
  for (const [key, value] of Object.entries(record)) {
    if (key !== "chart_hint" && Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}

/** The hover wash behind a bar: faint and theme-aware, instead of Recharts' default light-grey slab. */
const BAR_HOVER_CURSOR = { fill: "var(--text)", opacity: 0.08 } as const;

const TOOLTIP_STYLE = {
  contentStyle: {
    background: "var(--surface-2)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-input)",
    color: "var(--text)",
  },
  labelStyle: { color: "var(--text)" },
  itemStyle: { color: "var(--text)" },
  cursor: { fill: "var(--accent)", opacity: 0.08 },
} as const;

/** Pure, hook-free "would `GenericChart` actually render something for this payload" check —
 * mirrors `extract-rows.ts`'s `anyRowsPresent`/`hasSnapshotEntity` precedent: `RichCardView` needs
 * to decide whether to commit to the generic path or fall back to the legacy `card_type` switch
 * *before* rendering, and a hook-using component can't be called directly just to probe its own
 * "would I return null" answer. A hint that fails this check falls through to the existing
 * `card_type`-keyed path exactly like an unmapped/empty legacy card_type does today — never a
 * blank chart-shaped hole where a card used to render. */
export function chartHintUsable(data: unknown): boolean {
  if (!data || typeof data !== "object") {
    return false;
  }
  const parsed = chartHintSchema.safeParse((data as Record<string, unknown>).chart_hint);
  if (!parsed.success) {
    return false;
  }
  const hint = parsed.data;
  const rows = extractRows(data, hint.data_key ?? null);
  if (rows.length === 0) {
    return false;
  }
  if (hint.mark === "arc") {
    return Boolean(hint.theta && hint.color);
  }
  return Boolean(hint.x && hint.y);
}

/** A card may carry several charts at once (`chart_hints`, plural — e.g. the multi-year fundamentals
 * report: revenue, growth, EPS, margins, dividends). Each hint names its own series array via
 * `data_key`, so it is rendered exactly like the single-hint path: the card's data with that one
 * hint swapped in as `chart_hint`. Only hints that would actually draw are returned, in order. */
export function chartHintPayloads(data: unknown): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  const hints = record.chart_hints;
  if (!Array.isArray(hints)) {
    return [];
  }
  return hints
    .map((hint): Record<string, unknown> => ({ ...record, chart_hint: hint }))
    .filter((payload) => chartHintUsable(payload));
}

/** Adaptive chart renderer — reads `card.data.chart_hint` (validated against `chartHintSchema`)
 * to pick a `mark`/encoding independent of which tool produced the data, instead of every chart
 * shape needing its own hardcoded `card_type -> component` wiring (`card-registry.ts`). Colors
 * always resolve from design tokens via `resolveSeriesColor` — `chart_hint` never carries a color
 * value itself. Wrapped in the same `ChartWithDataSheet` shell every hardcoded chart card uses, so
 * the collapsible data-sheet table underneath is free. Callers should gate on `chartHintUsable`
 * first (see that function's own docstring) — this still defensively returns `null` on its own if
 * called without that gate, but never fabricates a chart from data that doesn't support one. */
export function GenericChart({ data }: Readonly<{ data: unknown }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  if (!data || typeof data !== "object") {
    return null;
  }
  const parsed = chartHintSchema.safeParse((data as Record<string, unknown>).chart_hint);
  if (!parsed.success) {
    return null;
  }
  const hint = parsed.data;
  const rows = extractRows(data, hint.data_key ?? null);
  if (rows.length === 0) {
    return null;
  }

  if (hint.mark === "arc") {
    return <ArcChart hint={hint} rows={rows} locale={locale} t={t} />;
  }
  return <SeriesChart hint={hint} rows={rows} locale={locale} t={t} />;
}

type Translator = ReturnType<typeof useTranslations>;

/** A chart's title carries its unit in brackets ("年度報酬（%）"); a percentage series should read
 * "35.4%" in the tooltip, not a bare "35.4". Only `%` is recognised — currency units are already in
 * the title and the axis, and guessing one would mislabel a value. */
export function unitSuffix(title: string | undefined | null): string {
  return title && /[（(][^）)]*%[^）)]*[）)]/.test(title) ? "%" : "";
}

/** A donut's weights are often fractions of one (an ETF's sector weights sum to ~1, 0.387 = 38.7%),
 * which read as a bare "0.387" next to a name. When the slices are such a set of fractions, show
 * each as its share of the whole; otherwise show the amount itself (dividend per share, …). */
export function isFractionSet(values: readonly number[]): boolean {
  const total = values.reduce((sum, v) => sum + v, 0);
  return values.length > 0 && values.every((v) => v >= 0 && v <= 1) && total > 0.95 && total < 1.05;
}

export function formatShare(value: number, total: number, locale: string): string {
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format((value / total) * 100)}%`;
}

function ArcChart({
  hint,
  rows,
  locale,
  t,
}: Readonly<{
  hint: ChartHint;
  rows: Record<string, unknown>[];
  locale: string;
  t: Translator;
}>): ReactNode {
  if (!hint.theta || !hint.color) {
    return null;
  }
  const slices = groupArcSlices(rows, hint.color.field, hint.theta.field, t("otherCategory"));
  if (slices.length === 0) {
    return null;
  }
  const colorField = hint.color.field;
  const categoryName = (label: string): string => {
    const translated = valueLabel(t, colorField, label);
    return translated === label ? fieldLabel(t, label) : translated;
  };
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const fractions = isFractionSet(slices.map((slice) => slice.value));
  const valueText = (value: number): string =>
    fractions ? formatShare(value, total, locale) : formatNumber(value, locale);
  const sheetRows = slices.map((s) => ({
    [colorField]: categoryName(s.label),
    value: fractions ? formatShare(s.value, total, locale) : s.value,
  }));

  const sliceColors = distinctChartColors(slices.map((slice) => slice.label));
  return (
    <ChartWithDataSheet rows={sheetRows}>
      {hint.title ? <ChartTitle title={hint.title} /> : null}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <div className="h-[200px] w-[200px] shrink-0">
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={slices}
                dataKey="value"
                nameKey="label"
                innerRadius="55%"
                outerRadius="100%"
                strokeWidth={0}
              >
                {slices.map((slice) => (
                  <Cell key={slice.label} fill={sliceColors[slice.label] ?? "var(--chart-1)"} />
                ))}
              </Pie>
              <Tooltip
                {...TOOLTIP_STYLE}
                formatter={(value, name) => {
                  const amount = asNumber(value) ?? 0;
                  const text = fractions
                    ? formatShare(amount, total, locale)
                    : `${formatNumber(amount, locale)}（${formatShare(amount, total, locale)}）`;
                  return [text, categoryName(String(name))];
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-1 flex-col gap-2">
          {slices.map((slice) => {
            const color = sliceColors[slice.label];
            return (
              <div key={slice.label} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[13px] text-[var(--text)]">
                  <span
                    aria-hidden
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  {categoryName(slice.label)}
                </span>
                <span className="shrink-0 font-num text-[13px] tabular-nums" style={{ color }}>
                  {valueText(slice.value)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </ChartWithDataSheet>
  );
}

function SeriesChart({
  hint,
  rows,
  locale,
  t,
}: Readonly<{
  hint: ChartHint;
  rows: Record<string, unknown>[];
  locale: string;
  t: Translator;
}>): ReactNode {
  if (!hint.x || !hint.y) {
    return null;
  }
  const { points, seriesLabels } = pivotByColor(
    rows,
    hint.x.field,
    hint.y.field,
    hint.color?.field ?? null,
  );
  if (points.length === 0 || seriesLabels.length === 0) {
    return null;
  }
  const sheetRows: Record<string, unknown>[] = points.map((p) => ({ ...p }));

  return (
    <ChartWithDataSheet rows={sheetRows}>
      {hint.title ? <ChartTitle title={hint.title} /> : null}
      <div
        style={{ width: "100%", height: hint.diverging ? Math.max(160, points.length * 32) : 280 }}
      >
        <ResponsiveContainer>
          {renderPlot(hint, points, seriesLabels, locale, t)}
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

function renderPlot(
  hint: ChartHint,
  points: SeriesPoint[],
  seriesLabels: string[],
  locale: string,
  t: Translator,
): React.ReactElement {
  if (hint.diverging) {
    return (
      <BarChart data={points} layout="vertical" margin={{ left: 8, right: 24 }}>
        <CartesianGrid horizontal={false} stroke="var(--grid)" />
        <XAxis
          type="number"
          tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          tickFormatter={(v: number) => formatNumber(v, locale)}
          stroke="var(--border)"
        />
        <YAxis
          type="category"
          dataKey="x"
          tickFormatter={(key: string) => fieldLabel(t, key)}
          tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          width={110}
          stroke="var(--border)"
        />
        <ReferenceLine x={0} stroke="var(--border)" />
        <Tooltip
          {...TOOLTIP_STYLE}
          cursor={BAR_HOVER_CURSOR}
          labelFormatter={(key) => fieldLabel(t, String(key))}
          formatter={(value) => formatNumber(asNumber(value), locale)}
        />
        <Bar dataKey="value" radius={4}>
          {points.map((point, index) => {
            const value = asNumber(point.value);
            return (
              <Cell
                // biome-ignore lint/suspicious/noArrayIndexKey: tie-breaker only; `points` is a fixed, never-reordered snapshot.
                key={`${point.x}|${index}`}
                fill={resolveSeriesColor(point.x, true, value)}
                aria-label={
                  (value ?? 0) >= 0
                    ? `${fieldLabel(t, point.x)} ▲ ${formatNumber(value, locale)}`
                    : `${fieldLabel(t, point.x)} ▼ ${formatNumber(value, locale)}`
                }
              />
            );
          })}
        </Bar>
      </BarChart>
    );
  }

  const colors = distinctChartColors(seriesLabels);
  const commonAxes = (
    <>
      <CartesianGrid vertical={false} stroke="var(--grid)" />
      <XAxis
        dataKey="x"
        tick={{ fill: "var(--text-muted)", fontSize: 12 }}
        stroke="var(--border)"
      />
      <YAxis
        tick={{ fill: "var(--text-muted)", fontSize: 12 }}
        tickFormatter={(v: number) => formatCompactNumber(v, locale)}
        width={56}
        stroke="var(--border)"
      />
      <Tooltip
        {...TOOLTIP_STYLE}
        {...(hint.mark === "bar" ? { cursor: BAR_HOVER_CURSOR } : {})}
        formatter={(value) => `${formatNumber(asNumber(value), locale)}${unitSuffix(hint.title)}`}
        labelFormatter={(key) => String(key)}
      />
      {seriesLabels.length > 1 ? (
        <Legend
          formatter={(value) => fieldLabel(t, String(value))}
          wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
        />
      ) : null}
    </>
  );

  if (hint.mark === "line") {
    return (
      <LineChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
        {commonAxes}
        {seriesLabels.map((label) => (
          <Line
            key={label}
            type="monotone"
            dataKey={label}
            name={label}
            stroke={colors[label] ?? "var(--chart-1)"}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </LineChart>
    );
  }
  if (hint.mark === "area") {
    return (
      <AreaChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
        {commonAxes}
        {seriesLabels.map((label) => {
          const color = colors[label] ?? "var(--chart-1)";
          return (
            <Area
              key={label}
              type="monotone"
              dataKey={label}
              name={label}
              stroke={color}
              fill={color}
              fillOpacity={0.2}
            />
          );
        })}
      </AreaChart>
    );
  }
  if (hint.mark === "point") {
    return (
      <ScatterChart margin={{ left: 8, right: 8, bottom: 8 }}>
        {commonAxes}
        {seriesLabels.map((label) => (
          <Scatter key={label} name={label} data={points} dataKey={label} fill={colors[label]} />
        ))}
      </ScatterChart>
    );
  }
  // mark === "bar"
  return (
    <BarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
      {commonAxes}
      {seriesLabels.map((label) => (
        <Bar key={label} dataKey={label} name={label} fill={colors[label]} radius={4} />
      ))}
    </BarChart>
  );
}

function ChartTitle({ title }: Readonly<{ title: string }>): ReactNode {
  return <p className="mb-2 font-medium text-[13px] text-[var(--text-muted)]">{title}</p>;
}
