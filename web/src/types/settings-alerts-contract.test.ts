import { describe, expect, it } from "vitest";
import { PER_STOCK_TEMPLATE_KINDS, TW_ONLY_TEMPLATE_KINDS } from "@/lib/alerts/condition";
import fixture from "./__fixtures__/alerts-api.json";
import {
  alertPreferenceSchema,
  alertsResponseSchema,
  notificationBadgeSchema,
  notificationsResponseSchema,
  schedulesResponseSchema,
} from "./settings";

/** Contract test: `__fixtures__/alerts-api.json` holds REAL responses captured from the backend
 * (regenerate with `pytest tests/api/_gen_alerts_fixture.py` in backend/). If the backend changes a
 * shape, this fails here — at the seam — instead of as a silent runtime mismatch in the panel. */
describe("alerts API contract (real backend responses)", () => {
  it("parses every alert template the backend serialises", () => {
    const { alerts } = alertsResponseSchema.parse(fixture.alerts);
    const kinds = alerts.map((a) => a.condition.kind).sort();
    expect(kinds).toEqual([
      "attention_entry",
      "close_price",
      "daily_change",
      "disposition_entry",
      "etf_premium_discount",
      "institutional_streak",
      "margin_balance_change",
      "material_announcement",
      "month_revenue_published",
      "volume_surge",
    ]);
  });

  it("keeps decimal thresholds and the default lifetime", () => {
    const { alerts } = alertsResponseSchema.parse(fixture.alerts);
    const price = alerts.find((a) => a.condition.kind === "close_price");
    expect(price?.condition).toMatchObject({ comparator: "above", value: 600.5 });
    expect(price?.expires_at).not.toBeNull();
    expect(price?.trigger_mode).toBe("once_per_day");
    expect(price?.watchlist_id).toBeNull();
  });

  it("parses schedules, notifications, badge, and default preferences", () => {
    expect(schedulesResponseSchema.parse(fixture.schedules).schedules).toHaveLength(1);
    expect(notificationsResponseSchema.parse(fixture.notifications).notifications).toEqual([]);
    expect(notificationBadgeSchema.parse(fixture.badge).unread_notifications).toBe(0);
    const prefs = alertPreferenceSchema.parse(fixture.preferences_default);
    expect(prefs.timezone).toBe("Asia/Taipei");
    expect(prefs.channels).toEqual([]);
  });

  it("returns the mark-read count", () => {
    expect(fixture.mark_read).toEqual({ marked: 0 });
  });

  it("offers exactly the backend's Taiwan-only templates as Taiwan-only (drift guard)", () => {
    expect([...TW_ONLY_TEMPLATE_KINDS].sort()).toEqual(fixture.tw_only_kinds);
    expect([...PER_STOCK_TEMPLATE_KINDS].sort()).toEqual(fixture.per_stock_kinds);
  });
});
