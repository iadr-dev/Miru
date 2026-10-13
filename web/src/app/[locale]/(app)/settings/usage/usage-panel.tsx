"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LlmUsageCard } from "@/app/[locale]/(app)/settings/usage/llm-usage-card";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Link } from "@/i18n/navigation";
import { getTodayUsage, getUsageQuota } from "@/lib/api/settings";
import { featureLabel } from "@/lib/feature-labels";

/** 用量 — the plan's limits, how much of today's allowance is used, and (below) the caller's own
 * model usage. Allowances use a real used/limit bar because the backend now reports consumption. */
export function UsagePanel(): ReactNode {
  const t = useTranslations("settings.usage");
  const tc = useTranslations("settings.common");

  const query = useQuery({ queryKey: ["settings", "usage-quota"], queryFn: getUsageQuota });
  const today = useQuery({ queryKey: ["settings", "usage-today"], queryFn: getTodayUsage });

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
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("title")} description={t("description")}>
        <SettingsRow label={t("tier")}>
          <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
            {t(`tierOption.${quota.tier}`)}
          </span>
        </SettingsRow>
        <AllowanceRow
          label={t("chatLimit")}
          used={today.data?.chats_used ?? null}
          limit={quota.chat_limit_per_day}
          unlimitedLabel={t("unlimited")}
          summary={(used, limit) => t("usedOfLimit", { used, limit })}
        />
        <SettingsRow label={t("byokActive")}>
          <span className="text-[14px] text-[var(--text)]">
            {quota.byok_active ? tc("yes") : tc("no")}
          </span>
        </SettingsRow>
        {/* Stacked rather than side by side: a long feature list beside its label squeezed the
            label to one character per line and overflowed the card. */}
        {quota.features.length > 0 ? (
          <div className="flex flex-col gap-2 py-4 last:pb-0">
            <p className="font-medium text-[14px] text-[var(--text)]">{t("features")}</p>
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
              {quota.features.map((f) => (
                <li
                  key={f}
                  className="rounded-full border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--text-muted)]"
                >
                  {featureLabel(t, f)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {quota.tier !== "pro" ? (
          <p className="pt-2 text-[13px]">
            <Link
              href="/settings/billing"
              className="text-[var(--accent-text)] underline-offset-2 hover:underline"
            >
              {t("upgrade")}
            </Link>
          </p>
        ) : null}
      </SettingsSection>

      <LlmUsageCard />
    </div>
  );
}

/** One daily allowance: "2 / 3" with a bar, or the unlimited label with no bar. */
function AllowanceRow({
  label,
  used,
  limit,
  unlimitedLabel,
  summary,
}: {
  label: string;
  used: number | null;
  limit: number | null;
  unlimitedLabel: string;
  summary: (used: number, limit: number) => string;
}): ReactNode {
  if (limit === null) {
    return (
      <SettingsRow label={label}>
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {unlimitedLabel}
        </span>
      </SettingsRow>
    );
  }
  const shown = used ?? 0;
  const percent = limit > 0 ? Math.min(100, Math.round((shown / limit) * 100)) : 0;
  const nearLimit = percent >= 100;
  return (
    <SettingsRow label={label}>
      <div className="flex w-full min-w-44 flex-col gap-1.5 sm:items-end">
        <span className="font-num text-[14px] text-[var(--text)] tabular-nums">
          {used === null ? `— / ${limit}` : summary(shown, limit)}
        </span>
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={shown}
          className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)] sm:w-44"
        >
          <div
            className="h-full rounded-full"
            style={{
              width: `${percent}%`,
              background: nearLimit ? "var(--warn)" : "var(--accent)",
            }}
          />
        </div>
      </div>
    </SettingsRow>
  );
}
