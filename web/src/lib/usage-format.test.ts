import { describe, expect, it } from "vitest";
import { formatCostMicros, formatTokens, shortModelName } from "@/lib/usage-format";

describe("formatTokens", () => {
  it("keeps small numbers exact", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(999)).toBe("999");
  });

  it("compacts thousands, millions and billions", () => {
    expect(formatTokens(1234)).toBe("1.2k");
    expect(formatTokens(1000)).toBe("1k");
    expect(formatTokens(150_000)).toBe("150k");
    expect(formatTokens(3_400_000)).toBe("3.4M");
    expect(formatTokens(2_000_000_000)).toBe("2B");
  });
});

describe("formatCostMicros", () => {
  it("shows sub-dollar charges with four decimals", () => {
    expect(formatCostMicros(140)).toBe("$0.0001");
    expect(formatCostMicros(1_500_000 / 1000)).toBe("$0.0015");
  });

  it("shows dollars with two decimals and zero as $0", () => {
    expect(formatCostMicros(2_500_000)).toBe("$2.50");
    expect(formatCostMicros(0)).toBe("$0");
  });
});

describe("shortModelName", () => {
  it("drops a duplicated provider prefix only", () => {
    expect(shortModelName("openrouter", "openrouter/auto")).toBe("auto");
    expect(shortModelName("gemini", "gemini-3.5-flash")).toBe("gemini-3.5-flash");
  });

  it("names a Claude id as the product, with or without a minor version or a date", () => {
    expect(shortModelName("anthropic", "claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(shortModelName("anthropic", "claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(shortModelName("anthropic", "claude-haiku-5-5")).toBe("Claude Haiku 5.5");
    expect(shortModelName("anthropic", "claude-sonnet-4-6")).toBe("Claude Sonnet 4.6");
    expect(shortModelName("anthropic", "claude-sonnet-5")).toBe("Claude Sonnet 5");
    expect(shortModelName("anthropic", "claude-haiku-4-5-20251001")).toBe("Claude Haiku 4.5");
  });

  it("leaves an id it does not recognise as it is", () => {
    expect(shortModelName("anthropic", "claude-future-9")).toBe("claude-future-9");
  });
});
