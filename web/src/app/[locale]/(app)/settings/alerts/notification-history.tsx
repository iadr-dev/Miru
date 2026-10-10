"use client";

import { BellSimpleIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { toNotificationView } from "@/components/shell/notification-payload";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Link } from "@/i18n/navigation";
import { listNotifications, markNotificationsRead } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { parseServerDate } from "@/lib/parse-server-date";

const HISTORY_LIMIT = 100;
const HISTORY_KEY = ["notifications", "history"] as const;

/** 通知紀錄 / Notification history — everything the alerts and schedules sent, newest first, with
 * read state. The bell shows the latest 20; this is the full list. Text renders as inert text and
 * each body already carries the non-advice footer. */
export function NotificationHistory(): ReactNode {
  const t = useTranslations("settings.alerts.history");
  const tc = useTranslations("settings.common");
  const format = useFormatter();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: HISTORY_KEY,
    queryFn: () => listNotifications({ limit: HISTORY_LIMIT }),
  });
  const markRead = useMutation({
    mutationFn: markNotificationsRead,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: HISTORY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["notifications", "badge"] });
      void queryClient.invalidateQueries({ queryKey: ["notifications", "list"] });
    },
  });

  if (query.isLoading) {
    return <SettingsLoading rows={3} />;
  }
  if (query.isError) {
    return <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />;
  }

  const items = (query.data ?? []).map(toNotificationView);
  if (items.length === 0) {
    return (
      <EmptyState compact icon={<BellSimpleIcon size={20} weight="regular" />} title={t("empty")} />
    );
  }
  const unread = items.filter((item) => item.unread).length;

  return (
    <div>
      {unread > 0 ? (
        <div className="mb-2 flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => markRead.mutate({ all: true })}
            disabled={markRead.isPending}
          >
            {t("markAllRead")}
          </Button>
        </div>
      ) : null}
      <ul className="divide-y divide-[var(--border)]">
        {items.map((item) => (
          <li key={item.id} className="flex flex-col gap-1 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span
                className={cn(
                  "text-[14px] text-[var(--text)]",
                  item.unread ? "font-semibold" : "font-normal",
                )}
              >
                {item.title}
              </span>
              <span className="shrink-0 font-num text-[12px] text-[var(--text-muted)]">
                {format.dateTime(parseServerDate(item.deliveredAt), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </span>
            </div>
            <p className="line-clamp-4 whitespace-pre-line text-[13px] text-[var(--text-muted)]">
              {item.body}
            </p>
            <div className="flex items-center gap-3 text-[12px]">
              {item.href ? (
                <Link
                  href={item.href}
                  className="text-[var(--accent-text)] underline-offset-2 hover:underline"
                  onClick={() => {
                    if (item.unread) {
                      markRead.mutate({ ids: [item.id] });
                    }
                  }}
                >
                  {t("open")}
                </Link>
              ) : null}
              {item.unread ? (
                <button
                  type="button"
                  className="text-[var(--text-muted)] underline-offset-2 hover:underline"
                  onClick={() => markRead.mutate({ ids: [item.id] })}
                >
                  {t("markRead")}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[12px] text-[var(--text-muted)]">
        {t("limitNote", { count: HISTORY_LIMIT })}
      </p>
    </div>
  );
}
