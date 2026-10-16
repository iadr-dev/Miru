import { describe, expect, it } from "vitest";
import { dashboard, releases, revisions, seriesResponse } from "@/test/us-macro-fixtures";
import { healthStatusSchema } from "@/types/market/shared";
import {
  isRevisionSeriesId,
  usEconomicReleasesResponseSchema,
  usMacroDashboardResponseSchema,
  usMacroSeriesResponseSchema,
  usSeriesRevisionsResponseSchema,
} from "@/types/market/us-macro";

describe("US macro schemas", () => {
  it("parses the documented dashboard shape", () => {
    const parsed = usMacroDashboardResponseSchema.parse(dashboard());
    expect(parsed.panels.map((panel) => panel.group)).toEqual([
      "yield_curve_recession",
      "inflation",
      "labor",
      "growth",
    ]);
    expect(parsed.yield_curve[0]?.state).toBe("inverted");
    expect(parsed.recession_bands[1]?.ongoing).toBe(true);
  });

  it("parses an unavailable dashboard with no panels", () => {
    const parsed = usMacroDashboardResponseSchema.parse({
      data_available: false,
      unavailable_reason: "not_ingested",
      as_of: null,
      recession_note: "note",
      currentness_caveat: "caveat",
    });
    expect(parsed.panels).toEqual([]);
    expect(parsed.data_available).toBe(false);
  });

  it("rejects an unknown yield-curve state and a malformed point", () => {
    const bad = dashboard();
    expect(() =>
      usMacroDashboardResponseSchema.parse({
        ...bad,
        yield_curve: [{ ...bad.yield_curve[0], state: "steep" }],
      }),
    ).toThrow();
    expect(() =>
      usMacroSeriesResponseSchema.parse({ ...seriesResponse("X"), points: [{ date: "d" }] }),
    ).toThrow();
  });

  it("parses series, releases and revisions", () => {
    expect(
      usMacroSeriesResponseSchema.parse(seriesResponse("CPIAUCSL")).metadata?.available_units,
    ).toEqual(["lin", "pc1", "pch"]);
    expect(
      usEconomicReleasesResponseSchema.parse(releases()).releases.map((r) => r.status),
    ).toEqual(["released", "upcoming"]);
    expect(usSeriesRevisionsResponseSchema.parse(revisions()).rows[0]?.vintages).toHaveLength(2);
  });

  it("knows the 13 revision-tracked series", () => {
    expect(isRevisionSeriesId("PAYEMS")).toBe(true);
    expect(isRevisionSeriesId("DGS10")).toBe(false);
  });

  it("accepts the new unavailable health status", () => {
    expect(healthStatusSchema.parse("unavailable")).toBe("unavailable");
  });
});
