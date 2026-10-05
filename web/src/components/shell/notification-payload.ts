import type { Notification } from "@/types/settings";

/** A notification's `payload` is an open record on the wire (`{title, body, deep_link}` for alerts
 * and schedules). Read it defensively: anything missing or mistyped degrades to an empty string /
 * no link rather than throwing in the bell. Text is rendered as inert text — never as markup. */

export type NotificationView = {
  id: string;
  title: string;
  body: string;
  unread: boolean;
  deliveredAt: string;
  /** In-app route (locale-less), or null when the notification has no usable deep link. */
  href: string | null;
};

function readText(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

/** `deep_link` is `{route, symbol?, thread_id?}`. Only routes this app actually has are honoured;
 * an unknown route yields no link so a tap never lands on a dead page. */
export function deepLinkHref(payload: Record<string, unknown>): string | null {
  const link = payload.deep_link;
  if (typeof link !== "object" || link === null) {
    return null;
  }
  const { route, symbol, thread_id: threadId } = link as Record<string, unknown>;
  if (route === "stock" && typeof symbol === "string" && symbol.trim() !== "") {
    return `/stock/${encodeURIComponent(symbol)}`;
  }
  // A schedule's notification points at the one thread every run of it is appended to.
  if (route === "thread" && typeof threadId === "string" && threadId.trim() !== "") {
    return `/chat?thread=${encodeURIComponent(threadId)}`;
  }
  return null;
}

export function toNotificationView(notification: Notification): NotificationView {
  return {
    id: notification.id,
    title: readText(notification.payload, "title"),
    body: readText(notification.payload, "body"),
    unread: notification.read_at === null,
    deliveredAt: notification.delivered_at,
    href: deepLinkHref(notification.payload),
  };
}

/** "9+" past nine so the badge never outgrows its circle. */
export function badgeLabel(count: number): string {
  return count > 9 ? "9+" : String(count);
}
