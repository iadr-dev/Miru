import type { StrategyKey, StrategyWatchItem } from "@/types/strategy-watch";

/** The level a strategy's intraday check is waiting on (what the chart draws and the table shows).
 * Breakouts wait above a high; the bounce watches its lower band; the divergence watches a new low. */
export type KeyLevel = {
  key: "upper_band" | "lower_band" | "low_20d" | "range_high_20d" | "flag_high";
  value: number | null;
};

const KEY_LEVEL: Record<StrategyKey, KeyLevel["key"]> = {
  volatility_squeeze: "upper_band",
  oversold_bounce: "lower_band",
  ad_divergence: "low_20d",
  chip_accumulation: "range_high_20d",
  momentum_flag: "flag_high",
};

export function keyLevel(item: StrategyWatchItem): KeyLevel {
  const key = KEY_LEVEL[item.strategy];
  return { key, value: item[key] };
}

/** How far the latest price (or the last close before the first check) is from the key level, in
 * percent: positive means above it. */
export function distanceToLevel(item: StrategyWatchItem): number | null {
  const { value } = keyLevel(item);
  const price = item.price ?? item.close;
  if (value === null || value <= 0) return null;
  return ((price - value) / value) * 100;
}

/** Trigger conditions met out of all triggers at the latest check (guards are shown apart). */
export function triggersMet(item: StrategyWatchItem): { met: number; total: number } {
  const triggers = item.conditions.filter((c) => c.kind === "trigger");
  return { met: triggers.filter((c) => c.met).length, total: triggers.length };
}

/** The strategy tab to open first: the one asked for, else the first with stocks on its list. */
export function initialStrategy(
  counts: Record<string, number>,
  order: readonly StrategyKey[],
  wanted: string | null,
): StrategyKey {
  const asked = order.find((key) => key === wanted);
  if (asked) return asked;
  return order.find((key) => (counts[key] ?? 0) > 0) ?? order[0] ?? "volatility_squeeze";
}
