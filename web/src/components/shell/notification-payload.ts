import type { Notification, NotificationDeepLink, NotificationLayout } from "@/types/settings";

/** A notification's `payload` arrives typed (`title`, `body`, `deep_link`, `layout`, `footer`) and
 * already degraded field by field by the schema. This turns it into what the bell draws. Text is
 * rendered as inert text, never as markup. */

export type NotificationView = {
  id: string;
  title: string;
  body: string;
  unread: boolean;
  deliveredAt: string;
  /** In-app route (locale-less), or null when the notification has no usable deep link. */
  href: string | null;
  /** The structured twin of `body`, or null to draw `title` and `body` as plain text. */
  layout: NotificationLayout | null;
  /** The non-advice footer a layout is shown under. */
  footer: string;
};

function present(value: string | null | undefined): value is string {
  return typeof value === "string" && value.trim() !== "";
}

/** Only routes this app actually has are honoured; one without what it needs yields no link so a
 * tap never lands on a dead page. */
export function deepLinkHref(link: NotificationDeepLink | null | undefined): string | null {
  if (!link) {
    return null;
  }
  // A 策略盯盤 notification opens the watch page, on its stock and strategy when it names one.
  if (link.route === "watch") {
    const search = new URLSearchParams();
    if (present(link.symbol)) search.set("symbol", link.symbol);
    if (present(link.strategy)) search.set("strategy", link.strategy);
    const query = search.toString();
    return query ? `/watch?${query}` : "/watch";
  }
  if (link.route === "stock" && present(link.symbol)) {
    return `/stock/${encodeURIComponent(link.symbol)}`;
  }
  // A schedule's notification points at the one thread every run of it is appended to.
  if (link.route === "thread" && present(link.thread_id)) {
    return `/chat?thread=${encodeURIComponent(link.thread_id)}`;
  }
  return null;
}

/** A layout with nothing to show is no layout: the plain body is shown instead. */
function usableLayout(layout: NotificationLayout | null | undefined): NotificationLayout | null {
  if (!layout) {
    return null;
  }
  const blocks = layout.blocks.filter((block) => block.head !== "" || block.lines.length > 0);
  return blocks.length > 0 ? { blocks } : null;
}

export function toNotificationView(notification: Notification): NotificationView {
  const { payload } = notification;
  return {
    id: notification.id,
    title: payload.title,
    body: payload.body,
    unread: notification.read_at === null,
    deliveredAt: notification.delivered_at,
    href: deepLinkHref(payload.deep_link),
    layout: usableLayout(payload.layout),
    footer: payload.footer,
  };
}

/** "9+" past nine so the badge never outgrows its circle. */
export function badgeLabel(count: number): string {
  return count > 9 ? "9+" : String(count);
}
