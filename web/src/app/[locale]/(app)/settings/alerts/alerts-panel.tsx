"use client";

import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsSection } from "@/components/settings/settings-section";
import type { AlertFormPreset } from "./alert-form";
import { AlertList } from "./alert-list";
import { NotificationHistory } from "./notification-history";
import { ScheduleList } from "./schedule-list";

/** 提醒與排程 / Alerts & Schedules. Open to every signed-in user (ADR-0028) — there is no tier gate
 * on the feature itself; what a tier gates is the *data* a notification carries. Each list owns its
 * own loading / empty / error states, so one failing list never blanks the other. */
export function AlertsPanel(): ReactNode {
  const t = useTranslations("settings.alerts");
  const preset = presetFromParams(useSearchParams());

  return (
    <>
      <SettingsSection title={t("title")} description={t("description")} className="mb-6">
        <AlertList preset={preset} />
      </SettingsSection>
      <SettingsSection
        title={t("schedules.title")}
        description={t("schedules.description")}
        className="mb-6"
      >
        <ScheduleList />
      </SettingsSection>
      <SettingsSection title={t("history.title")} description={t("history.description")}>
        <NotificationHistory />
      </SettingsSection>
    </>
  );
}

/** `?alert=symbol&symbol=2330&market=TW`, `?alert=group&group=<id>` or `?alert=perStock&group=<id>` —
 * how the watchlist's bell and group menu open the form already pointed at what was clicked. */
function presetFromParams(params: URLSearchParams): AlertFormPreset | undefined {
  const kind = params.get("alert");
  const group = params.get("group") ?? undefined;
  if (kind === "symbol") {
    const symbol = params.get("symbol")?.trim().toUpperCase();
    const market = params.get("market") === "US" ? "US" : "TW";
    return symbol ? { scope: "symbol", symbol, market } : undefined;
  }
  if (kind === "group") {
    return { scope: "watchlist", groupId: group };
  }
  if (kind === "perStock") {
    return { scope: "perStock", groupId: group };
  }
  return undefined;
}
