import { describe, expect, it } from "vitest";
import {
  matchesThemeQuery,
  sampleMemberSymbols,
  THEME_HEAT_SAMPLE,
  themeHeat,
} from "@/lib/theme-heat";

describe("themeHeat", () => {
  it("averages the quoted members and counts them", () => {
    const heat = themeHeat([2, -1, 5]);
    expect(heat.average).toBeCloseTo(2, 10);
    expect(heat.quoted).toBe(3);
  });

  it("leaves members without a quote out of both the sum and the count", () => {
    const heat = themeHeat([4, null, undefined, Number.NaN, 2]);
    expect(heat.average).toBeCloseTo(3, 10);
    expect(heat.quoted).toBe(2);
  });

  it("has no average (never zero) when nothing is quoted", () => {
    expect(themeHeat([])).toEqual({ average: null, quoted: 0 });
    expect(themeHeat([null, undefined])).toEqual({ average: null, quoted: 0 });
  });

  it("keeps a real zero change as zero", () => {
    expect(themeHeat([0, 0])).toEqual({ average: 0, quoted: 2 });
  });
});

describe("sampleMemberSymbols", () => {
  const members = Array.from({ length: 30 }, (_, i) => ({ symbol: `${1000 + i}` }));
  it("takes the first members up to the bound", () => {
    expect(sampleMemberSymbols(members)).toHaveLength(THEME_HEAT_SAMPLE);
    expect(sampleMemberSymbols(members)[0]).toBe("1000");
    expect(sampleMemberSymbols(members, 3)).toEqual(["1000", "1001", "1002"]);
    expect(sampleMemberSymbols(members.slice(0, 2))).toEqual(["1000", "1001"]);
    expect(sampleMemberSymbols(members, -1)).toEqual([]);
  });
});

describe("matchesThemeQuery", () => {
  const theme = { name_zh: "雷射光學", name_en: "Laser Optics" };
  it("matches either name, ignoring case and padding", () => {
    expect(matchesThemeQuery(theme, "雷射")).toBe(true);
    expect(matchesThemeQuery(theme, "  optic ")).toBe(true);
    expect(matchesThemeQuery(theme, "battery")).toBe(false);
  });
  it("matches everything for a blank query", () => {
    expect(matchesThemeQuery(theme, "  ")).toBe(true);
  });
});
