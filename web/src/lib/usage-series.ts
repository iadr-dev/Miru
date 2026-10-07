/** Turns the per-model usage summary into chart series: one stacked bar segment per model,
 * coloured by its provider (several models of one provider get shades of that colour), with the
 * long tail folded into one "other" segment so the legend stays readable. */
import { providerStyle, reportsCost } from "@/lib/provider-style";
import { shortModelName } from "@/lib/usage-format";
import type { DailyUsage, ModelTotals, UsageTotals } from "@/types/usage";

export const OTHER_KEY = "__other__";
export const MAX_SERIES = 8;
const SHADE_STEPS = [100, 72, 52, 38];

export type Series = {
  /** The key in `daily[].by_model` ("provider/model"), or `OTHER_KEY`. */
  key: string;
  provider: string;
  /** Model name for display; "" for the folded "other" series. */
  model: string;
  color: string;
};

/** `models` must already be ordered biggest first (the API does this). */
export function buildSeries(models: ModelTotals[], max: number = MAX_SERIES): Series[] {
  const shown = models.length > max ? models.slice(0, max - 1) : models;
  const seenPerProvider = new Map<string, number>();
  const series: Series[] = shown.map((m) => {
    const index = seenPerProvider.get(m.provider) ?? 0;
    seenPerProvider.set(m.provider, index + 1);
    const base = providerStyle(m.provider).color;
    const strength = SHADE_STEPS[Math.min(index, SHADE_STEPS.length - 1)] ?? 100;
    return {
      key: `${m.provider}/${m.model}`,
      provider: m.provider,
      model: shortModelName(m.provider, m.model),
      color: strength === 100 ? base : `color-mix(in srgb, ${base} ${strength}%, var(--surface))`,
    };
  });
  if (models.length > max) {
    series.push({ key: OTHER_KEY, provider: "", model: "", color: "var(--text-muted)" });
  }
  return series;
}

export type ChartPoint = { label: string; total: number } & Record<string, number | string>;

/** One row per day: tokens per series key (0 when unused), the folded remainder under
 * `OTHER_KEY`, and the day's total - all from `by_model`. */
export function buildPoints(
  daily: DailyUsage[],
  series: Series[],
  formatDate: (isoDate: string) => string,
): ChartPoint[] {
  const known = new Set(series.map((s) => s.key));
  return daily.map((day) => {
    const point: ChartPoint = { label: formatDate(day.date), total: 0 };
    for (const s of series) {
      point[s.key] = 0;
    }
    for (const [key, tokens] of Object.entries(day.by_model)) {
      const target = known.has(key) ? key : OTHER_KEY;
      if (target === OTHER_KEY && !known.has(OTHER_KEY)) {
        continue;
      }
      point[target] = (Number(point[target]) || 0) + tokens;
      point.total += tokens;
    }
    return point;
  });
}

/** The figures for the picked model, or the whole window when nothing is picked. */
export function selectionTotals(
  models: ModelTotals[],
  windowTotals: UsageTotals,
  selectedKey: string | null,
): UsageTotals {
  if (selectedKey === null) {
    return windowTotals;
  }
  const match = models.find((m) => `${m.provider}/${m.model}` === selectedKey);
  return match ?? windowTotals;
}

export type CostKind = "reported" | "estimated" | "unpriced";
export type ProviderCost = { provider: string; costMicros: number; kind: CostKind };

/** How one model's cost should be shown: the provider's own charge, a list-price estimate, or
 * nothing known. A provider-reported charge always wins over an estimate. */
export function modelCost(m: ModelTotals): { kind: CostKind; micros: number } {
  if (reportsCost(m.provider)) {
    return { kind: "reported", micros: m.cost_micros };
  }
  if (m.priced) {
    return { kind: "estimated", micros: m.estimated_cost_micros };
  }
  return { kind: "unpriced", micros: 0 };
}

/** Cost per provider (biggest first). A provider is "reported" if it returns charges, "estimated"
 * if at least one of its models has a list price, otherwise "unpriced" (unknown, not free). */
export function costByProvider(models: ModelTotals[]): ProviderCost[] {
  const sums = new Map<string, { micros: number; kind: CostKind }>();
  for (const m of models) {
    const cost = modelCost(m);
    const current = sums.get(m.provider) ?? { micros: 0, kind: "unpriced" as CostKind };
    const kind = current.kind === "unpriced" ? cost.kind : current.kind;
    sums.set(m.provider, { micros: current.micros + cost.micros, kind });
  }
  return [...sums.entries()]
    .map(([provider, v]) => ({ provider, costMicros: v.micros, kind: v.kind }))
    .sort((a, b) => b.costMicros - a.costMicros);
}
