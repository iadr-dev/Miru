import { describe, expect, it } from "vitest";
import { chartHintSchema } from "@/types/chart-hint";

describe("chartHintSchema smoke test", () => {
  it("accepts a valid bar hint", () => {
    const r = chartHintSchema.safeParse({
      mark: "bar",
      x: { field: "month", type: "ordinal" },
      y: { field: "revenue", type: "quantitative" },
    });
    expect(r.success).toBe(true);
  });

  it("accepts a valid arc hint", () => {
    const r = chartHintSchema.safeParse({
      mark: "arc",
      theta: { field: "amount", type: "quantitative" },
      color: { field: "year", type: "nominal" },
    });
    expect(r.success).toBe(true);
  });

  it("rejects line with nominal x", () => {
    const r = chartHintSchema.safeParse({
      mark: "line",
      x: { field: "cat", type: "nominal" },
      y: { field: "v", type: "quantitative" },
    });
    expect(r.success).toBe(false);
  });

  it("rejects arc with x set", () => {
    const r = chartHintSchema.safeParse({
      mark: "arc",
      x: { field: "x", type: "quantitative" },
      theta: { field: "a", type: "quantitative" },
      color: { field: "c", type: "nominal" },
    });
    expect(r.success).toBe(false);
  });

  it("rejects diverging with color set", () => {
    const r = chartHintSchema.safeParse({
      mark: "bar",
      x: { field: "x", type: "ordinal" },
      y: { field: "y", type: "quantitative" },
      diverging: true,
      color: { field: "c", type: "nominal" },
    });
    expect(r.success).toBe(false);
  });
});
