import { describe, expect, it } from "vitest";
import { chartHintUsable, pivotByColor } from "@/app/[locale]/(app)/chat/cards/generic-chart";
import { LABELED_FIELDS } from "@/lib/field-labels";

/** The shape `get_event_study` emits: bars of mean / median excess return per horizon, the label
 * carrying n, drawn by the generic chart renderer (no dedicated card component). */
const payload = {
  chart_hint: {
    mark: "bar",
    title: "事後超額報酬 Excess return vs market median（%）",
    data_key: "chart_rows",
    x: { field: "label", type: "nominal" },
    y: { field: "excess_return_pct", type: "quantitative" },
    color: { field: "statistic", type: "nominal" },
    diverging: false,
  },
  chart_rows: [
    {
      label: "D+5 (n=101)",
      horizon_days: 5,
      statistic: "mean",
      excess_return_pct: -0.05,
      n_events: 101,
    },
    {
      label: "D+5 (n=101)",
      horizon_days: 5,
      statistic: "median",
      excess_return_pct: -0.3,
      n_events: 101,
    },
    {
      label: "D+20 (n=41)",
      horizon_days: 20,
      statistic: "mean",
      excess_return_pct: 2.92,
      n_events: 41,
    },
    {
      label: "D+20 (n=41)",
      horizon_days: 20,
      statistic: "median",
      excess_return_pct: 1.1,
      n_events: 41,
    },
  ],
  events: [{ symbol: "2330", event_date: "2026-09-01" }],
};

describe("event study chart payload", () => {
  it("is drawn by the generic renderer from chart_rows, not the first array field", () => {
    expect(chartHintUsable(payload)).toBe(true);
  });

  it("is not drawn when every horizon was too small to report", () => {
    expect(chartHintUsable({ ...payload, chart_hint: null, chart_rows: [] })).toBe(false);
  });

  it("pivots into one point per horizon label with a mean and a median series", () => {
    const { points, seriesLabels } = pivotByColor(
      payload.chart_rows,
      "label",
      "excess_return_pct",
      "statistic",
    );
    expect(seriesLabels).toEqual(["mean", "median"]);
    expect(points.map((p) => p.x)).toEqual(["D+5 (n=101)", "D+20 (n=41)"]);
    expect(points[1]).toMatchObject({ mean: 2.92, median: 1.1 });
  });

  it("has a label for every field the legend and data sheet show", () => {
    for (const key of ["statistic", "mean", "median", "excess_return_pct", "n_events", "label"]) {
      expect(LABELED_FIELDS.has(key), key).toBe(true);
    }
  });
});
