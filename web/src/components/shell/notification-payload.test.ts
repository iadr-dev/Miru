import { describe, expect, it } from "vitest";
import type { Notification } from "@/types/settings";
import { badgeLabel, deepLinkHref, toNotificationView } from "./notification-payload";

const base: Notification = {
  id: "n1",
  user_id: "u",
  source: "alert",
  source_id: "a1",
  channel: "in_app",
  dedupe_key: "k",
  payload: {},
  delivered_at: "2026-10-03T09:00:00Z",
  read_at: null,
};

describe("deepLinkHref", () => {
  it("links a stock notification to its detail page", () => {
    expect(deepLinkHref({ deep_link: { route: "stock", symbol: "2330" } })).toBe("/stock/2330");
  });

  it("opens the stock a strategy-watch alert names; the morning list has no link yet", () => {
    expect(
      deepLinkHref({
        deep_link: { route: "watch", symbol: "2330", strategy: "volatility_squeeze" },
      }),
    ).toBe("/stock/2330");
    expect(deepLinkHref({ deep_link: { route: "watch" } })).toBeNull();
  });

  it("encodes the symbol", () => {
    expect(deepLinkHref({ deep_link: { route: "stock", symbol: "A/B" } })).toBe("/stock/A%2FB");
  });

  it("returns null for unknown routes, missing symbols, and malformed links", () => {
    expect(deepLinkHref({ deep_link: { route: "thread" } })).toBeNull();
    expect(deepLinkHref({ deep_link: { route: "stock" } })).toBeNull();
    expect(deepLinkHref({ deep_link: { route: "stock", symbol: "  " } })).toBeNull();
    expect(deepLinkHref({ deep_link: "stock/2330" })).toBeNull();
    expect(deepLinkHref({ deep_link: null })).toBeNull();
    expect(deepLinkHref({})).toBeNull();
  });
});

describe("toNotificationView", () => {
  it("reads title/body and marks unread from read_at", () => {
    const view = toNotificationView({
      ...base,
      payload: {
        title: "2330 - 收盤價高於 600",
        body: "…\n本通知為你設定條件的資料提醒，非投資建議。",
      },
    });
    expect(view.title).toBe("2330 - 收盤價高於 600");
    expect(view.body).toContain("非投資建議");
    expect(view.unread).toBe(true);
    expect(toNotificationView({ ...base, read_at: "2026-10-03T10:00:00Z" }).unread).toBe(false);
  });

  it("degrades a malformed payload to empty text instead of throwing", () => {
    const view = toNotificationView({ ...base, payload: { title: 42, body: null } });
    expect(view.title).toBe("");
    expect(view.body).toBe("");
    expect(view.href).toBeNull();
  });
});

describe("badgeLabel", () => {
  it("caps at 9+", () => {
    expect(badgeLabel(3)).toBe("3");
    expect(badgeLabel(9)).toBe("9");
    expect(badgeLabel(10)).toBe("9+");
  });
});

describe("deepLinkHref for a schedule's thread", () => {
  it("opens the schedule's one thread", () => {
    expect(deepLinkHref({ deep_link: { route: "thread", thread_id: "t 1" } })).toBe(
      "/chat?thread=t%201",
    );
  });
});
