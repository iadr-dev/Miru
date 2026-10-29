import { describe, expect, it } from "vitest";
import { type Notification, notificationSchema } from "@/types/settings";
import { badgeLabel, deepLinkHref, toNotificationView } from "./notification-payload";

const wire = {
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

function parse(payload: unknown, readAt: string | null = null): Notification {
  return notificationSchema.parse({ ...wire, payload, read_at: readAt });
}

describe("deepLinkHref", () => {
  it("links a stock notification to its detail page", () => {
    expect(deepLinkHref({ route: "stock", symbol: "2330" })).toBe("/stock/2330");
  });

  it("opens the watch page on the stock and strategy a strategy-watch alert names", () => {
    expect(deepLinkHref({ route: "watch", symbol: "2330", strategy: "volatility_squeeze" })).toBe(
      "/watch?symbol=2330&strategy=volatility_squeeze",
    );
    expect(deepLinkHref({ route: "watch" })).toBe("/watch");
  });

  it("encodes the symbol", () => {
    expect(deepLinkHref({ route: "stock", symbol: "A/B" })).toBe("/stock/A%2FB");
  });

  it("opens the schedule's one thread", () => {
    expect(deepLinkHref({ route: "thread", thread_id: "t 1" })).toBe("/chat?thread=t%201");
  });

  it("returns null for a route without what it needs and for no link", () => {
    expect(deepLinkHref({ route: "thread" })).toBeNull();
    expect(deepLinkHref({ route: "stock" })).toBeNull();
    expect(deepLinkHref({ route: "stock", symbol: "  " })).toBeNull();
    expect(deepLinkHref({ route: "notifications" })).toBeNull();
    expect(deepLinkHref(null)).toBeNull();
    expect(deepLinkHref(undefined)).toBeNull();
  });
});

describe("toNotificationView", () => {
  it("reads title/body and marks unread from read_at", () => {
    const payload = {
      title: "2330 - 收盤價高於 600",
      body: "…\n本通知為你設定條件的資料提醒，非投資建議。",
    };
    const view = toNotificationView(parse(payload));
    expect(view.title).toBe("2330 - 收盤價高於 600");
    expect(view.body).toContain("非投資建議");
    expect(view.unread).toBe(true);
    expect(view.layout).toBeNull();
    expect(toNotificationView(parse(payload, "2026-10-03T10:00:00Z")).unread).toBe(false);
  });

  it("degrades a malformed payload to empty text instead of throwing", () => {
    const view = toNotificationView(
      parse({ title: 42, body: null, deep_link: { route: "elsewhere" }, layout: "x" }),
    );
    expect(view.title).toBe("");
    expect(view.body).toBe("");
    expect(view.href).toBeNull();
    expect(view.layout).toBeNull();
  });

  it("carries a layout and its footer", () => {
    const layout = { blocks: [{ head: "🔔 台積電", lines: ["a"], fold: false }] };
    const view = toNotificationView(parse({ title: "t", body: "b", layout, footer: "非投資建議" }));
    expect(view.layout).toEqual(layout);
    expect(view.footer).toBe("非投資建議");
  });

  it("drops a layout that does not match the schema and one with nothing to show", () => {
    const invalid = { blocks: [{ head: 5, lines: "no", fold: "yes" }] };
    expect(toNotificationView(parse({ title: "t", body: "b", layout: invalid })).layout).toBeNull();
    const empty = { blocks: [{ head: "", lines: [], fold: false }] };
    expect(toNotificationView(parse({ title: "t", body: "b", layout: empty })).layout).toBeNull();
  });

  it("keeps the blocks that have something to show", () => {
    const layout = {
      blocks: [
        { head: "", lines: [], fold: false },
        { head: "h", lines: [], fold: false },
      ],
    };
    expect(toNotificationView(parse({ layout })).layout?.blocks).toHaveLength(1);
  });
});

describe("badgeLabel", () => {
  it("caps at 9+", () => {
    expect(badgeLabel(3)).toBe("3");
    expect(badgeLabel(9)).toBe("9");
    expect(badgeLabel(10)).toBe("9+");
  });
});
