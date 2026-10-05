import { describe, expect, it } from "vitest";
import { distinctChartColors, hashToChartToken } from "./chart-token-hash";

describe("distinctChartColors", () => {
  it("never gives two series in one chart the same color (0050 vs 00878 collided before)", () => {
    expect(hashToChartToken("0050")).toBe(hashToChartToken("00878")); // the reported collision
    const colors = distinctChartColors(["0050", "00878"]);
    expect(colors["0050"]).not.toBe(colors["00878"]);
  });

  it("resolves a collision to a contrasting hue, not the next (similar) token", () => {
    const colors = distinctChartColors(["0050", "00878"]);
    // 0050 keeps --chart-6 (gray-blue); 00878 must not become the sky-blue --chart-7 beside it.
    expect(colors["0050"]).toBe("var(--chart-6)");
    expect(colors["00878"]).toBe("var(--chart-3)");
  });

  it("keeps a label's stable hashed color whenever it is still free", () => {
    expect(distinctChartColors(["ROE"]).ROE).toBe(hashToChartToken("ROE"));
  });

  it("is deterministic for the same ordered labels", () => {
    const labels = ["ROE", "毛利率", "營業利益率", "0050", "00878"];
    expect(distinctChartColors(labels)).toEqual(distinctChartColors(labels));
  });

  it("gives ten distinct series ten distinct colors", () => {
    const labels = Array.from({ length: 10 }, (_, i) => `series-${i}`);
    const colors = Object.values(distinctChartColors(labels));
    expect(new Set(colors).size).toBe(10);
  });
});
