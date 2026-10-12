"use client";

import { BellIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { getNotificationBadge, listNotifications, markNotificationsRead } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { FRESHNESS } from "@/lib/freshness";
import { parseServerDate } from "@/lib/parse-server-date";
import { badgeLabel, type NotificationView, toNotificationView } from "./notification-payload";

const BADGE_KEY = ["notifications", "badge"] as const;
const LIST_KEY = ["notifications", "list"] as const;
const INBOX_LIMIT = 20;

/** Notification inbox. The unread count comes from the badge
 * endpoint and refreshes on window focus only — no polling interval. The list loads when the menu
 * opens. Radix `DropdownMenu` gives `Esc`-close and focus-return-to-trigger for free. Notification
 * text renders as inert text; the non-advice footer rides inside each body. */
export function NotificationsBell(): ReactNode {
  const t = useTranslations("shell.notifications");
  const format = useFormatter();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const badge = useQuery({
    queryKey: BADGE_KEY,
    queryFn: getNotificationBadge,
    staleTime: FRESHNESS.standard,
    refetchOnWindowFocus: true,
    retry: false,
  });
  const list = useQuery({
    queryKey: LIST_KEY,
    queryFn: () => listNotifications({ limit: INBOX_LIMIT }),
    enabled: open,
    retry: false,
  });

  const markRead = useMutation({
    mutationFn: markNotificationsRead,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: BADGE_KEY });
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    },
  });

  const unread = badge.data?.unread_notifications ?? 0;
  const items: NotificationView[] = (list.data ?? []).map(toNotificationView);

  const openItem = (item: NotificationView): void => {
    if (item.unread) {
      markRead.mutate({ ids: [item.id] });
    }
    if (item.href) {
      router.push(item.href);
    }
  };

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={unread > 0 ? t("unread", { count: unread }) : t("label")}
          title={t("label")}
          className={cn(
            "relative inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            "data-[state=open]:bg-[var(--surface-2)] data-[state=open]:text-[var(--text)]",
          )}
        >
          <BellIcon size={20} weight="regular" />
          {unread > 0 ? (
            <span
              aria-hidden
              className={cn(
                "absolute top-1.5 right-1.5 inline-flex min-w-4 items-center justify-center",
                "rounded-full bg-[var(--accent)] px-1 font-num text-[10px] text-[var(--bg)] leading-4",
              )}
            >
              {badgeLabel(unread)}
            </span>
          ) : null}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="bottom"
          align="end"
          sideOffset={8}
          className={cn(
            "z-50 w-80 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] shadow-lg",
          )}
        >
          {items.length === 0 ? (
            <p className="p-4 text-center text-[var(--text-muted)] text-sm">{t("empty")}</p>
          ) : (
            <ul className="max-h-96 divide-y divide-[var(--border)] overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <DropdownMenu.Item
                    onSelect={() => openItem(item)}
                    className={cn(
                      "!outline-none flex cursor-pointer flex-col gap-1 px-4 py-3",
                      "data-[highlighted]:bg-[var(--surface-2)]",
                    )}
                  >
                    <span
                      className={cn(
                        "text-[13px] text-[var(--text)]",
                        item.unread ? "font-semibold" : "font-normal",
                      )}
                    >
                      {item.title}
                    </span>
                    <span className="whitespace-pre-line text-[12px] text-[var(--text-muted)]">
                      {item.body}
                    </span>
                    <span className="font-num text-[11px] text-[var(--text-muted)]">
                      {format.dateTime(parseServerDate(item.deliveredAt), {
                        dateStyle: "medium",
                        timeStyle: "short",
                      })}
                    </span>
                  </DropdownMenu.Item>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center justify-between gap-2 border-[var(--border)] border-t px-2 py-1">
            <DropdownMenu.Item
              onSelect={() => router.push("/settings/alerts")}
              className="!outline-none min-h-11 cursor-pointer rounded-[var(--radius-button)] px-3 text-[13px] text-[var(--text-muted)] data-[highlighted]:bg-[var(--surface-2)]"
            >
              <span className="inline-flex min-h-11 items-center">{t("manage")}</span>
            </DropdownMenu.Item>
            {unread > 0 ? (
              <DropdownMenu.Item
                onSelect={() => markRead.mutate({ all: true })}
                className="!outline-none min-h-11 cursor-pointer rounded-[var(--radius-button)] px-3 text-[13px] text-[var(--accent-text)] data-[highlighted]:bg-[var(--surface-2)]"
              >
                <span className="inline-flex min-h-11 items-center">{t("markAllRead")}</span>
              </DropdownMenu.Item>
            ) : null}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
