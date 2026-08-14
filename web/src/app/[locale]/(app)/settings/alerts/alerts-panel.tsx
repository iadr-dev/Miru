"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsSection } from "@/components/settings/settings-section";
import { RequiresProNotice, SettingsLoading } from "@/components/settings/settings-states";
import { useRouter } from "@/i18n/navigation";
import { listAlerts, SettingsApiError } from "@/lib/api/settings";
import { AlertList } from "./alert-list";
import { ScheduleList } from "./schedule-list";

/** 提醒與排程 / Alerts & Schedules — Pro-gated. A 403 `feature_not_entitled` on the probe request
 * renders a calm upgrade notice instead of the builder (never 馬賽克, per interaction-spec §2.13). */
export function AlertsPanel(): ReactNode {
  const t = useTranslations("settings.alerts");
  const router = useRouter();

  const entitlementProbe = useQuery({
    queryKey: ["settings", "alerts"],
    queryFn: listAlerts,
    retry: false,
  });

  if (entitlementProbe.isLoading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={2} />
      </SettingsSection>
    );
  }

  const notEntitled =
    entitlementProbe.isError &&
    entitlementProbe.error instanceof SettingsApiError &&
    entitlementProbe.error.code === "feature_not_entitled";

  if (notEntitled) {
    return <RequiresProNotice onUpgrade={() => router.push("/settings/billing")} />;
  }

  return (
    <>
      <SettingsSection title={t("title")} description={t("description")} className="mb-6">
        <AlertList />
      </SettingsSection>
      <SettingsSection title={t("schedules.title")} description={t("schedules.description")}>
        <ScheduleList />
      </SettingsSection>
    </>
  );
}
