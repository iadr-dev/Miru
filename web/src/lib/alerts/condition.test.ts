import { describe, expect, it } from "vitest";
import { type AlertCondition, alertConditionSchema, alertRuleSchema } from "@/types/settings";
import {
  buildAlertPatch,
  buildCondition,
  type ConditionDraft,
  conditionSummary,
  DEFAULT_DRAFT,
  draftFromCondition,
  type EditInputs,
  fieldsFor,
  mergeStaleEdit,
  ruleStatus,
  sameCondition,
  templatesFor,
} from "./condition";

const draft = (patch: Partial<ConditionDraft>): ConditionDraft => ({ ...DEFAULT_DRAFT, ...patch });

describe("buildCondition", () => {
  it("keeps decimal thresholds (v1 truncated 123.5 to 123)", () => {
    const result = buildCondition(draft({ kind: "close_price", value: "123.5" }));
    expect(result).toEqual({
      ok: true,
      condition: { kind: "close_price", comparator: "above", value: 123.5 },
    });
  });

  it("flags empty and non-numeric input as required, never as zero", () => {
    expect(buildCondition(draft({ kind: "close_price", value: "" }))).toEqual({
      ok: false,
      errors: { value: "required" },
    });
    expect(buildCondition(draft({ kind: "close_price", value: "abc" }))).toEqual({
      ok: false,
      errors: { value: "required" },
    });
  });

  it("rejects out-of-range values with the same bounds the backend enforces", () => {
    expect(buildCondition(draft({ kind: "close_price", value: "0" }))).toEqual({
      ok: false,
      errors: { value: "range" },
    });
    expect(buildCondition(draft({ kind: "daily_change", percent: "101" }))).toEqual({
      ok: false,
      errors: { percent: "range" },
    });
    expect(buildCondition(draft({ kind: "volume_surge", multiple: "1", window: "20" }))).toEqual({
      ok: false,
      errors: { multiple: "range" },
    });
    expect(buildCondition(draft({ kind: "volume_surge", multiple: "2", window: "4" }))).toEqual({
      ok: false,
      errors: { window: "range" },
    });
    expect(buildCondition(draft({ kind: "institutional_streak", days: "1" }))).toEqual({
      ok: false,
      errors: { days: "range" },
    });
    expect(buildCondition(draft({ kind: "institutional_streak", days: "21" }))).toEqual({
      ok: false,
      errors: { days: "range" },
    });
  });

  it("builds every template from its own fields only", () => {
    expect(
      buildCondition(draft({ kind: "daily_change", moveDirection: "down", percent: "5" })),
    ).toEqual({ ok: true, condition: { kind: "daily_change", direction: "down", percent: 5 } });
    expect(buildCondition(draft({ kind: "volume_surge", multiple: "2.5", window: "10" }))).toEqual({
      ok: true,
      condition: { kind: "volume_surge", multiple: 2.5, window: 10 },
    });
    expect(
      buildCondition(
        draft({
          kind: "institutional_streak",
          investor: "trust",
          flowDirection: "sell",
          days: "4",
        }),
      ),
    ).toEqual({
      ok: true,
      condition: { kind: "institutional_streak", investor: "trust", direction: "sell", days: 4 },
    });
    expect(buildCondition(draft({ kind: "attention_entry" }))).toEqual({
      ok: true,
      condition: { kind: "attention_entry" },
    });
  });

  it("ignores stale fields belonging to other templates", () => {
    // `value` is left over from the price template; it must not block an attention rule.
    expect(buildCondition(draft({ kind: "attention_entry", value: "garbage" })).ok).toBe(true);
  });

  it("only requires the fields a template uses", () => {
    expect(fieldsFor("close_price")).toEqual(["value"]);
    expect(fieldsFor("attention_entry")).toEqual([]);
  });
});

describe("alertConditionSchema", () => {
  it("rejects the legacy free-text shape", () => {
    const legacy = { type: "price", metric: "close", comparator: "above", threshold: 600 };
    expect(alertConditionSchema.safeParse(legacy).success).toBe(false);
  });

  it("defaults the volume window like the backend", () => {
    const parsed = alertConditionSchema.parse({ kind: "volume_surge", multiple: 2 });
    expect(parsed).toEqual({ kind: "volume_surge", multiple: 2, window: 20 });
  });
});

describe("conditionSummary", () => {
  it("picks a fully-written message per variant", () => {
    expect(conditionSummary({ kind: "close_price", comparator: "below", value: 90.5 })).toEqual({
      key: "close_price.below",
      values: { value: 90.5 },
    });
    expect(
      conditionSummary({
        kind: "institutional_streak",
        investor: "foreign",
        direction: "buy",
        days: 5,
      }),
    ).toEqual({ key: "institutional_streak.foreign.buy", values: { days: 5 } });
    expect(conditionSummary({ kind: "attention_entry" })).toEqual({
      key: "attention_entry",
      values: {},
    });
  });
});

describe("ruleStatus", () => {
  const base = alertRuleSchema.parse({
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
  });
  const now = new Date("2026-10-03T00:00:00Z");

  it("maps engine lifecycle fields to the four UI states", () => {
    expect(ruleStatus(base, now)).toBe("active");
    expect(ruleStatus({ ...base, enabled: false }, now)).toBe("paused");
    expect(ruleStatus({ ...base, enabled: false, auto_paused_reason: "completed" }, now)).toBe(
      "completed",
    );
    expect(ruleStatus({ ...base, enabled: false, auto_paused_reason: "expired" }, now)).toBe(
      "expired",
    );
    expect(ruleStatus({ ...base, enabled: false, expires_at: "2026-10-02T00:00:00Z" }, now)).toBe(
      "expired",
    );
  });

  it("parses a watchlist-scoped rule with a null symbol", () => {
    const rule = alertRuleSchema.parse({
      ...base,
      symbol: null,
      market: null,
      watchlist_id: "wl-1",
    });
    expect(rule.symbol).toBeNull();
    expect(rule.watchlist_id).toBe("wl-1");
  });
});

describe("draftFromCondition", () => {
  it("round-trips every template through the form strings", () => {
    const conditions: AlertCondition[] = [
      { kind: "close_price", comparator: "below", value: 90.5 },
      { kind: "daily_change", direction: "down", percent: 7.25 },
      { kind: "volume_surge", multiple: 2.5, window: 10 },
      { kind: "institutional_streak", investor: "dealer", direction: "sell", days: 4 },
      { kind: "attention_entry" },
    ];
    for (const condition of conditions) {
      expect(buildCondition(draftFromCondition(condition))).toEqual({ ok: true, condition });
    }
  });
});

describe("sameCondition", () => {
  it("ignores key order but not values", () => {
    const a: AlertCondition = { kind: "close_price", comparator: "above", value: 600 };
    const sameButReordered = {
      value: 600,
      comparator: "above",
      kind: "close_price",
    } as AlertCondition;
    expect(sameCondition(a, sameButReordered)).toBe(true);
    expect(sameCondition(a, { ...a, value: 601 })).toBe(false);
    expect(sameCondition(a, { kind: "attention_entry" })).toBe(false);
  });
});

describe("buildAlertPatch", () => {
  const rule = alertRuleSchema.parse({
    id: "a",
    user_id: "u",
    symbol: "2330",
    market: "TW",
    condition: { kind: "close_price", comparator: "above", value: 600 },
    trigger_mode: "once_per_day",
    quiet_hours: null,
    daily_limit: null,
    last_triggered: null,
    version: 3,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  });
  const unchanged: EditInputs = {
    condition: rule.condition,
    triggerMode: "once_per_day",
    expiry: "keep",
  };

  it("returns null when nothing changed, so no version bump and no re-arm", () => {
    expect(buildAlertPatch(rule, unchanged)).toBeNull();
  });

  it("sends only changed fields, always with the expected version", () => {
    expect(buildAlertPatch(rule, { ...unchanged, triggerMode: "once" })).toEqual({
      expected_version: 3,
      trigger_mode: "once",
    });
    const moved: AlertCondition = { kind: "close_price", comparator: "above", value: 650 };
    expect(buildAlertPatch(rule, { ...unchanged, condition: moved })).toEqual({
      expected_version: 3,
      condition: moved,
    });
  });

  it("maps the expiry choice: keep omits it, never sends null, a preset sends days", () => {
    expect(buildAlertPatch(rule, { ...unchanged, expiry: "never" })).toEqual({
      expected_version: 3,
      expires_in_days: null,
    });
    expect(buildAlertPatch(rule, { ...unchanged, expiry: "365" })).toEqual({
      expected_version: 3,
      expires_in_days: 365,
    });
  });
});

describe("parameterless templates (注意股 / 處置股 / 月營收)", () => {
  const flags = ["attention_entry", "disposition_entry", "month_revenue_published"] as const;

  it("build without any field and need none", () => {
    for (const kind of flags) {
      expect(fieldsFor(kind)).toEqual([]);
      expect(buildCondition(draft({ kind, value: "garbage" }))).toEqual({
        ok: true,
        condition: { kind },
      });
    }
  });

  it("summarise under a message keyed by their own kind, round-trip, and parse", () => {
    for (const kind of flags) {
      const condition: AlertCondition = { kind };
      expect(conditionSummary(condition)).toEqual({ key: kind, values: {} });
      expect(buildCondition(draftFromCondition(condition))).toEqual({ ok: true, condition });
      expect(alertConditionSchema.safeParse(condition).success).toBe(true);
    }
  });
});

describe("templatesFor", () => {
  it("offers every template for Taiwan", () => {
    expect(templatesFor("TW")).toContain("month_revenue_published");
    expect(templatesFor("TW")).toContain("institutional_streak");
  });

  it("hides every Taiwan-only template for a US symbol and keeps the price ones", () => {
    expect(templatesFor("US")).toEqual(["close_price", "daily_change", "volume_surge"]);
  });
});

describe("融資融券 change and ETF 折溢價 templates", () => {
  it("build from their own fields and reject out-of-range sizes like the backend", () => {
    expect(
      buildCondition(
        draft({
          kind: "margin_balance_change",
          marginSide: "short",
          moveDirection: "down",
          percent: "12.5",
        }),
      ),
    ).toEqual({
      ok: true,
      condition: { kind: "margin_balance_change", side: "short", direction: "down", percent: 12.5 },
    });
    expect(
      buildCondition(draft({ kind: "etf_premium_discount", navSide: "discount", percent: "2" })),
    ).toEqual({
      ok: true,
      condition: { kind: "etf_premium_discount", direction: "discount", percent: 2 },
    });
    expect(buildCondition(draft({ kind: "margin_balance_change", percent: "1001" }))).toEqual({
      ok: false,
      errors: { percent: "range" },
    });
    expect(buildCondition(draft({ kind: "etf_premium_discount", percent: "51" }))).toEqual({
      ok: false,
      errors: { percent: "range" },
    });
    expect(buildCondition(draft({ kind: "etf_premium_discount", percent: "" }))).toEqual({
      ok: false,
      errors: { percent: "required" },
    });
  });

  it("summarise under distinct, fully-written messages and round-trip through the form", () => {
    const conditions: AlertCondition[] = [
      { kind: "margin_balance_change", side: "margin", direction: "up", percent: 10 },
      { kind: "margin_balance_change", side: "short", direction: "down", percent: 5 },
      { kind: "etf_premium_discount", direction: "premium", percent: 1 },
      { kind: "etf_premium_discount", direction: "discount", percent: 2.5 },
    ];
    const keys = conditions.map((c) => conditionSummary(c).key);
    expect(keys).toEqual([
      "margin_balance_change.margin.up",
      "margin_balance_change.short.down",
      "etf_premium_discount.premium",
      "etf_premium_discount.discount",
    ]);
    for (const condition of conditions) {
      expect(buildCondition(draftFromCondition(condition))).toEqual({ ok: true, condition });
    }
  });

  it("are Taiwan-only, so a US symbol is not offered them", () => {
    expect(templatesFor("TW")).toContain("margin_balance_change");
    expect(templatesFor("TW")).toContain("etf_premium_discount");
    expect(templatesFor("US")).toEqual(["close_price", "daily_change", "volume_surge"]);
  });
});

describe("mergeStaleEdit", () => {
  const make = (value: number, mode: "once" | "once_per_day", version: number) =>
    alertRuleSchema.parse({
      id: "a",
      user_id: "u",
      symbol: "2330",
      market: "TW",
      condition: { kind: "close_price", comparator: "above", value },
      trigger_mode: mode,
      quiet_hours: null,
      daily_limit: null,
      last_triggered: null,
      version,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    });
  const base = make(600, "once_per_day", 3);
  const price = (value: number): AlertCondition => ({
    kind: "close_price",
    comparator: "above",
    value,
  });

  it("takes the other side's change for fields the user left alone", () => {
    const theirs = make(650, "once_per_day", 4);
    const merged = mergeStaleEdit(base, theirs, { condition: price(600), triggerMode: "once" });
    expect(merged.condition).toEqual(price(650));
    expect(merged.triggerMode).toBe("once");
    expect(merged.conflict).toBe(false);
  });

  it("keeps the user's value and flags it when both sides changed the same field", () => {
    const theirs = make(650, "once_per_day", 4);
    const merged = mergeStaleEdit(base, theirs, {
      condition: price(700),
      triggerMode: "once_per_day",
    });
    expect(merged.condition).toEqual(price(700));
    expect(merged.conflict).toBe(true);
  });
});
