import { describe, expect, it } from "vitest";
import { formatAmount } from "@/lib/format-amount";

describe("formatAmount", () => {
  it("uses the native 萬/億/兆 scale with a currency word in zh-TW", () => {
    expect(formatAmount(16_049_526_130, "zh-TW", "TWD")).toBe("160.50 億元");
    expect(formatAmount(3_200_000_000_000, "zh-TW", "USD")).toBe("3.20 兆美元");
    expect(formatAmount(52_000, "zh-TW", "TWD")).toBe("5.20 萬元");
    expect(formatAmount(800, "zh-TW", "TWD")).toBe("800 元");
  });

  it("uses compact currency elsewhere", () => {
    expect(formatAmount(16_049_526_130, "en", "TWD")).toBe("NT$16.05B");
    expect(formatAmount(3_200_000_000_000, "en", "USD")).toBe("$3.2T");
  });

  it("renders a dash for a missing value, never 0", () => {
    expect(formatAmount(null, "zh-TW", "TWD")).toBe("—");
    expect(formatAmount(undefined, "en", "USD")).toBe("—");
  });
});
