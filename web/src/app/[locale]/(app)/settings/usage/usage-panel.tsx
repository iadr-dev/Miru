"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { getUsageQuota } from "@/lib/api/settings";

/** 用量 — resolved LIMITS only (docs/web/settings/README.md: live consumption isn't exposed by the
 * backend). Never fabricate an X/N progress bar; render caps as informational text. */
export function UsagePanel(): ReactNode {
  const t = useTranslations("settings.usage");
  const tc = useTranslations("settings.common");

  const query = useQuery({ queryKey: ["settings", "usage-quota"], queryFn: getUsageQuota });

  if (query.isLoading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={4} />
      </SettingsSection>
    );
  }

  if (query.isError || !query.data) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />
      </SettingsSection>
    );
  }

  const quota = query.data;

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <SettingsRow label={t("tier")}>
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {t(`tierOption.${quota.tier}`)}
        </span>
      </SettingsRow>
      <SettingsRow label={t("chatLimit")}>
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {quota.chat_limit_per_day === null
            ? t("unlimited")
            : t("chatsPerDay", { count: quota.chat_limit_per_day })}
        </span>
      </SettingsRow>
      <SettingsRow label={t("requestsPerMinute")}>
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {quota.requests_per_minute}
        </span>
      </SettingsRow>
      <SettingsRow label={t("finmindClass")}>
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {t(`tierOption.${quota.finmind_class}`)}
        </span>
      </SettingsRow>
      <SettingsRow label={t("byokActive")}>
        <span className="text-[14px] text-[var(--text)]">
          {quota.byok_active ? tc("yes") : tc("no")}
        </span>
      </SettingsRow>
      {quota.features.length > 0 ? (
        <SettingsRow label={t("features")}>
          <span className="max-w-56 text-right text-[13px] text-[var(--text-muted)]">
            {quota.features.map((f) => featureLabel(t, f)).join(", ")}
          </span>
        </SettingsRow>
      ) : null}
      <p className="pt-4 text-[12px] text-[var(--text-muted)] leading-relaxed">
        {t("noLiveUsageNote")}
      </p>
    </SettingsSection>
  );
}

/** `EntitlementView.features` is a backend enum-key list (`accounts/entitlements.py`'s
 * `FEATURE_MIN_TIER`) — falls back to the raw key for a feature this map hasn't caught up to
 * yet, same pattern as `cardTypeLabel`. */
const LABELED_FEATURES = new Set([
  "live_quote_stream",
  "ai_summary",
  "swot",
  "full_score",
  "news_sentiment",
  "theme_discovery",
  "deep_research",
  "scheduler",
  "tick_minute_k",
]);

function featureLabel(t: ReturnType<typeof useTranslations>, feature: string): string {
  return LABELED_FEATURES.has(feature) ? t(`featureOption.${feature}`) : feature;
}
