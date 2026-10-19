import { describe, expect, it } from "vitest";
import { withSearchParams } from "@/lib/url-params";

describe("withSearchParams", () => {
  it("sets, overwrites and removes keys while keeping the others", () => {
    expect(withSearchParams("/stock/2330", "tab=technical&sub=compare", { cmp: "2330,2454" })).toBe(
      "/stock/2330?tab=technical&sub=compare&cmp=2330%2C2454",
    );
    expect(withSearchParams("/stock/2330", "tab=technical&sub=compare", { sub: null })).toBe(
      "/stock/2330?tab=technical",
    );
    expect(withSearchParams("/stock/2330", new URLSearchParams("tab=ai"), { tab: "news" })).toBe(
      "/stock/2330?tab=news",
    );
  });

  it("returns the bare path once no parameter is left", () => {
    expect(withSearchParams("/stock/2330", "tab=ai", { tab: null })).toBe("/stock/2330");
    expect(withSearchParams("/stock/2330", "", {})).toBe("/stock/2330");
  });
});
