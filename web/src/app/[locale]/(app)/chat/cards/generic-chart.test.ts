import { describe, expect, it } from "vitest";
import {
  chartHintPayloads,
  chartHintUsable,
  checkCardinality,
  formatShare,
  groupArcSlices,
  isFractionSet,
  pivotByColor,
  resolveSeriesColor,
  unitSuffix,
} from "@/app/[locale]/(app)/chat/cards/generic-chart";

describe("resolveSeriesColor", () => {
  it("returns the same categorical token for the same label across calls", () => {
    const first = resolveSeriesColor("外資", false, null);
    const second = resolveSeriesColor("外資", false, null);
    expect(first).toBe(second);
  });

  it("routes diverging=true to --up for a non-negative value", () => {
    expect(resolveSeriesColor("外資", true, 5)).toBe("var(--up)");
    expect(resolveSeriesColor("外資", true, 0)).toBe("var(--up)");
  });

  it("routes diverging=true to --down for a negative value", () => {
    expect(resolveSeriesColor("外資", true, -5)).toBe("var(--down)");
  });

  it("never returns --up/--down for a categorical (non-diverging) series", () => {
    const color = resolveSeriesColor("外資", false, -5);
    expect(color).not.toBe("var(--up)");
    expect(color).not.toBe("var(--down)");
  });
});

describe("checkCardinality", () => {
  it("caps arc at 8 categories", () => {
    expect(checkCardinality(8, "arc")).toEqual({ fits: true, limit: 8 });
    expect(checkCardinality(9, "arc")).toEqual({ fits: false, limit: 8 });
  });

  it("caps bar/line/area/point at 10 categories", () => {
    expect(checkCardinality(10, "bar")).toEqual({ fits: true, limit: 10 });
    expect(checkCardinality(11, "line")).toEqual({ fits: false, limit: 10 });
  });
});

describe("groupArcSlices", () => {
  it("sums theta per distinct color-field value", () => {
    const rows = [
      { year: 2023, total_dividend: 10 },
      { year: 2023, total_dividend: 5 },
      { year: 2024, total_dividend: 20 },
    ];
    const slices = groupArcSlices(rows, "year", "total_dividend", "其他");
    expect(slices).toEqual(
      expect.arrayContaining([
        { label: "2024", value: 20 },
        { label: "2023", value: 15 },
      ]),
    );
  });

  it("coerces a numeric category field (e.g. a year) into a string label", () => {
    const rows = [{ year: 2024, total_dividend: 1 }];
    const slices = groupArcSlices(rows, "year", "total_dividend", "其他");
    expect(slices[0]?.label).toBe("2024");
  });

  it("buckets categories beyond the arc cardinality cap into one trailing 'other' slice", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ category: `cat-${i}`, value: 1 }));
    const slices = groupArcSlices(rows, "category", "value", "其他");
    expect(slices).toHaveLength(8);
    expect(slices.at(-1)?.label).toBe("其他");
  });

  it("skips rows with a missing category or non-numeric value", () => {
    const rows = [
      { year: 2023, total_dividend: 10 },
      { year: null, total_dividend: 5 },
      { year: 2024, total_dividend: "not-a-number" },
    ];
    const slices = groupArcSlices(rows, "year", "total_dividend", "其他");
    expect(slices).toEqual([{ label: "2023", value: 10 }]);
  });

  it("returns an empty array for no usable rows", () => {
    expect(groupArcSlices([], "year", "total_dividend", "其他")).toEqual([]);
  });
});

describe("pivotByColor", () => {
  it("pivots long-format rows into one wide point per x, one column per color-field label", () => {
    const rows = [
      { trade_date: "2026-01", investor: "外資", net: 10 },
      { trade_date: "2026-01", investor: "投信", net: -5 },
      { trade_date: "2026-02", investor: "外資", net: 3 },
    ];
    const { points, seriesLabels } = pivotByColor(rows, "trade_date", "net", "investor");
    expect(seriesLabels).toEqual(["外資", "投信"]);
    expect(points).toHaveLength(2);
    const jan = points.find((p) => p.x === "2026-01");
    expect(jan?.外資).toBe(10);
    expect(jan?.投信).toBe(-5);
  });

  it("falls back to a single 'value' series when colorField is null", () => {
    const rows = [
      { trade_date: "2026-01", revenue: 100 },
      { trade_date: "2026-02", revenue: 200 },
    ];
    const { points, seriesLabels } = pivotByColor(rows, "trade_date", "revenue", null);
    expect(seriesLabels).toEqual(["value"]);
    expect(points).toEqual([
      { x: "2026-01", value: 100 },
      { x: "2026-02", value: 200 },
    ]);
  });

  it("caps series labels at the multi-series cardinality limit", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({
      x: "2026-01",
      category: `series-${i}`,
      y: 1,
    }));
    const { seriesLabels } = pivotByColor(rows, "x", "y", "category");
    expect(seriesLabels).toHaveLength(10);
  });
});

describe("chartHintUsable", () => {
  it("is true for a valid bar hint with matching row data", () => {
    const data = {
      chart_hint: {
        mark: "bar",
        x: { field: "trade_date", type: "temporal" },
        y: { field: "revenue", type: "quantitative" },
      },
      rows: [{ trade_date: "2026-01-01", revenue: 100 }],
    };
    expect(chartHintUsable(data)).toBe(true);
  });

  it("is false when chart_hint is absent", () => {
    expect(chartHintUsable({ rows: [{ a: 1 }] })).toBe(false);
  });

  it("is false when chart_hint is present but the row data is empty", () => {
    const data = {
      chart_hint: {
        mark: "bar",
        x: { field: "trade_date", type: "temporal" },
        y: { field: "revenue", type: "quantitative" },
      },
      rows: [],
    };
    expect(chartHintUsable(data)).toBe(false);
  });

  it("is false for an arc hint missing its required theta/color fields", () => {
    const data = {
      chart_hint: { mark: "arc" },
      rows: [{ year: 2024, total_dividend: 10 }],
    };
    expect(chartHintUsable(data)).toBe(false);
  });

  it("is true for a valid arc hint using data_key to select the right row list", () => {
    const data = {
      chart_hint: {
        mark: "arc",
        data_key: "year_summaries",
        theta: { field: "total_dividend", type: "quantitative" },
        color: { field: "year", type: "nominal" },
      },
      events: [{ some: "unrelated array" }],
      year_summaries: [{ year: 2024, total_dividend: 10 }],
    };
    expect(chartHintUsable(data)).toBe(true);
  });
});

describe("chartHintPayloads", () => {
  const data = {
    revenue_series: [
      { year: "2023", metric: "營收", value: 100 },
      { year: "2024", metric: "營收", value: 120 },
    ],
    eps_series: [],
    chart_hints: [
      {
        mark: "bar",
        title: "營收",
        data_key: "revenue_series",
        x: { field: "year", type: "ordinal" },
        y: { field: "value", type: "quantitative" },
      },
      {
        mark: "bar",
        title: "EPS",
        data_key: "eps_series",
        x: { field: "year", type: "ordinal" },
        y: { field: "value", type: "quantitative" },
      },
    ],
  };

  it("returns one single-hint payload per chart that would actually draw", () => {
    const payloads = chartHintPayloads(data);
    expect(payloads).toHaveLength(1); // the EPS series is empty, so it is dropped
    expect(payloads[0]?.chart_hint).toMatchObject({ title: "營收" });
    expect(chartHintUsable(payloads[0])).toBe(true);
  });

  it("is empty for a card without chart_hints", () => {
    expect(chartHintPayloads({ rows: [] })).toEqual([]);
    expect(chartHintPayloads(null)).toEqual([]);
  });
});

describe("donut value formatting", () => {
  it("treats a set of weights that sum to about one as fractions and shows shares", () => {
    const weights = [0.387, 0.121, 0.095, 0.093, 0.093, 0.078, 0.045, 0.088];
    expect(isFractionSet(weights)).toBe(true);
    expect(
      formatShare(
        0.387,
        weights.reduce((a, b) => a + b, 0),
        "zh-TW",
      ),
    ).toBe("38.7%");
  });

  it("leaves amounts (dividends per year) alone", () => {
    expect(isFractionSet([22, 17, 13, 11])).toBe(false);
    expect(isFractionSet([0.5, 0.3])).toBe(false); // sums to 0.8, not a whole
  });
});

describe("unitSuffix", () => {
  it("adds a percent sign for percentage charts and nothing otherwise", () => {
    expect(unitSuffix("年度報酬（%）")).toBe("%");
    expect(unitSuffix("營收季節指數偏離（%，0 = 全年平均）")).toBe("%");
    expect(unitSuffix("營收（億元（新台幣））")).toBe("");
    expect(unitSuffix("價格走勢（起點 = 100）：2330")).toBe("");
    expect(unitSuffix(undefined)).toBe("");
  });
});
