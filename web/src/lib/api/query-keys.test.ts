import { describe, expect, it } from "vitest";
import { queryKeys } from "./query-keys";

describe("queryKeys", () => {
  it("builds the same arrays the call sites used inline, so cache entries do not move", () => {
    expect(queryKeys.me).toEqual(["me"]);
    expect(queryKeys.thread("t1")).toEqual(["thread", "t1"]);
    expect(queryKeys.settings("pet-active")).toEqual(["settings", "pet-active"]);
    expect(queryKeys.settings("llm-usage", 30)).toEqual(["settings", "llm-usage", 30]);
    expect(queryKeys.symbolRead("stock_score", "2330", "TW", "growth")).toEqual([
      "stock_score",
      "2330",
      "TW",
      "growth",
    ]);
    expect(queryKeys.marketRead("etf_list", "TW")).toEqual(["etf_list", "TW"]);
    expect(queryKeys.read("macro_calendar", "2026-01-01", "2026-01-31")).toEqual([
      "macro_calendar",
      "2026-01-01",
      "2026-01-31",
    ]);
  });

  it("a settings section is a prefix of its parameterised keys, so one invalidation covers both", () => {
    const section = queryKeys.settings("memory");
    const key = queryKeys.settings("memory", "all", "");
    expect(key.slice(0, section.length)).toEqual([...section]);
  });
});
