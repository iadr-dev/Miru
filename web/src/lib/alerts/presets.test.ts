import { describe, expect, it } from "vitest";
import { type AlertRule, alertRuleSchema } from "@/types/settings";
import { activeRuleCount, presetsFor, rulesForSymbol } from "./presets";

const rule = (patch: Partial<Record<string, unknown>>): AlertRule =>
  alertRuleSchema.parse({
    id: "a",
    user_id: "u",
    symbol: "2330",
    market: "TW",
    condition: { kind: "attention_entry" },
    quiet_hours: null,
    daily_limit: null,
    last_triggered: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...patch,
  });

describe("presetsFor", () => {
  const kinds = (market: "TW" | "US", isEtf: boolean) =>
    presetsFor(market, isEtf).map((p) => p.kind);

  it("offers TW-only templates just for Taiwan stocks", () => {
    expect(kinds("TW", false)).toEqual([
      "attention_entry",
      "disposition_entry",
      "month_revenue_published",
      "institutional_streak",
      "margin_balance_change",
      "daily_change",
      "daily_change",
      "volume_surge",
    ]);
  });

  it("offers 折溢價 presets only for Taiwan ETFs (a stock has no NAV row)", () => {
    expect(kinds("TW", true)).toEqual([
      "etf_premium_discount",
      "etf_premium_discount",
      "daily_change",
      "daily_change",
      "volume_surge",
    ]);
    expect(kinds("TW", false)).not.toContain("etf_premium_discount");
  });

  it("offers only the price templates for US symbols, stock or ETF", () => {
    for (const isEtf of [false, true]) {
      expect(kinds("US", isEtf)).toEqual(["daily_change", "daily_change", "volume_surge"]);
    }
  });

  it("only offers conditions the backend accepts", async () => {
    const { alertConditionSchema } = await import("@/types/settings");
    for (const preset of presetsFor("TW", false)) {
      expect(alertConditionSchema.safeParse(preset).success).toBe(true);
    }
  });
});

describe("rulesForSymbol / activeRuleCount", () => {
  const rules = [
    rule({ id: "1" }),
    rule({ id: "2", enabled: false }),
    rule({ id: "3", symbol: "2317" }),
    rule({ id: "4", market: "US" }),
    rule({ id: "5", symbol: null, market: null, watchlist_id: "wl" }),
  ];

  it("matches on symbol and market, and skips watchlist-scoped rules", () => {
    expect(rulesForSymbol(rules, "2330", "TW").map((r) => r.id)).toEqual(["1", "2"]);
  });

  it("counts only enabled rules", () => {
    expect(activeRuleCount(rulesForSymbol(rules, "2330", "TW"))).toBe(1);
    expect(activeRuleCount([])).toBe(0);
  });
});
