import { describe, expect, it } from "vitest";
import { directionColor, directionGlyph, directionOf, formatSignedChange } from "@/lib/signed";

describe("directionOf", () => {
  it("maps sign to direction", () => {
    expect(directionOf(1.2)).toBe("up");
    expect(directionOf(-0.4)).toBe("down");
    expect(directionOf(0)).toBe("flat");
    expect(directionOf(-0)).toBe("flat");
  });

  it("treats a value that displays as zero as flat", () => {
    expect(directionOf(0.004, 2)).toBe("flat"); // shows as 0.00
    expect(directionOf(-0.004, 2)).toBe("flat");
    expect(directionOf(0.006, 2)).toBe("up"); // shows as 0.01
  });

  it("treats non-finite values as flat", () => {
    expect(directionOf(Number.NaN)).toBe("flat");
    expect(directionOf(Number.POSITIVE_INFINITY)).toBe("flat");
  });
});

describe("glyph and colour", () => {
  it("uses red-up / green-down tokens and a neutral flat", () => {
    expect(directionGlyph(2)).toBe("▲");
    expect(directionGlyph(-2)).toBe("▼");
    expect(directionGlyph(0)).toBe("–");
    expect(directionColor(2)).toBe("var(--up)");
    expect(directionColor(-2)).toBe("var(--down)");
    expect(directionColor(0)).toBe("var(--text-muted)");
  });
});

describe("formatSignedChange", () => {
  it("puts the sign in the glyph and shows the absolute value", () => {
    expect(formatSignedChange(1.234, "en", { decimals: 2, unit: "%" })).toBe("▲ 1.23%");
    expect(formatSignedChange(-0.4, "en", { decimals: 2, unit: "%" })).toBe("▼ 0.40%");
    expect(formatSignedChange(0, "en", { decimals: 2, unit: "%" })).toBe("– 0.00%");
    expect(formatSignedChange(-1500, "en", { spaced: false })).toBe("▼1,500");
  });
});
