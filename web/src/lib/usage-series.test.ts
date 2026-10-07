import { describe, expect, it } from "vitest";
import {
  buildPoints,
  buildSeries,
  costByProvider,
  MAX_SERIES,
  OTHER_KEY,
  selectionTotals,
} from "@/lib/usage-series";
import type { DailyUsage, ModelTotals } from "@/types/usage";

const totals = {
  calls: 1,
  input_tokens: 0,
  output_tokens: 0,
  cached_tokens: 0,
  reasoning_tokens: 0,
  cost_micros: 0,
};
const model = (provider: string, name: string): ModelTotals => ({
  provider,
  model: name,
  ...totals,
});
const day = (date: string, by_model: Record<string, number>): DailyUsage => ({
  date,
  by_provider: {},
  by_model,
  ...totals,
});

describe("buildSeries", () => {
  it("colours by provider and shades a second model of the same provider", () => {
    const series = buildSeries([
      model("openai", "gpt-5.4"),
      model("anthropic", "sonnet"),
      model("openai", "gpt-5.4-mini"),
    ]);
    expect(series.map((s) => s.key)).toEqual([
      "openai/gpt-5.4",
      "anthropic/sonnet",
      "openai/gpt-5.4-mini",
    ]);
    expect(series[0]?.color).toBe("var(--chart-2)");
    expect(series[2]?.color).toContain("color-mix");
    expect(series[2]?.color).toContain("var(--chart-2)");
    expect(series[1]?.color).toBe("var(--chart-1)");
  });

  it("shortens a model id that repeats its provider", () => {
    expect(buildSeries([model("openrouter", "openrouter/auto")])[0]?.model).toBe("auto");
  });

  it("folds the tail into one 'other' series past the cap", () => {
    const many = Array.from({ length: MAX_SERIES + 3 }, (_, i) => model("groq", `m${i}`));
    const series = buildSeries(many);
    expect(series).toHaveLength(MAX_SERIES);
    expect(series.at(-1)?.key).toBe(OTHER_KEY);
  });
});

describe("buildPoints", () => {
  it("fills zeros, totals the day, and folds unlisted models into other", () => {
    const models = Array.from({ length: MAX_SERIES + 1 }, (_, i) => model("groq", `m${i}`));
    const series = buildSeries(models);
    const points = buildPoints(
      [day("2026-10-06", {}), day("2026-10-07", { "groq/m0": 100, "groq/m8": 40, "groq/m7": 10 })],
      series,
      (d) => d.slice(5),
    );
    expect(points[0]).toMatchObject({ label: "10-06", total: 0, "groq/m0": 0 });
    expect(points[1]).toMatchObject({ total: 150, "groq/m0": 100 });
    expect(points[1]?.[OTHER_KEY]).toBe(50);
  });
});

describe("selectionTotals", () => {
  const all = { ...totals, calls: 9 };
  const picked = { ...model("openai", "gpt-5.4"), calls: 2 };

  it("returns the window totals with no selection, or an unknown one", () => {
    expect(selectionTotals([picked], all, null).calls).toBe(9);
    expect(selectionTotals([picked], all, "x/y").calls).toBe(9);
  });

  it("returns the picked model's own figures", () => {
    expect(selectionTotals([picked], all, "openai/gpt-5.4").calls).toBe(2);
  });
});

describe("costByProvider", () => {
  it("sums per provider and marks providers that never report a charge", () => {
    const costs = costByProvider([
      { ...model("openrouter", "auto"), cost_micros: 1400 },
      { ...model("openrouter", "x"), cost_micros: 100 },
      model("openai", "gpt-5.4-mini"),
    ]);
    expect(costs[0]).toEqual({ provider: "openrouter", costMicros: 1500, reported: true });
    expect(costs[1]).toEqual({ provider: "openai", costMicros: 0, reported: false });
  });
});
