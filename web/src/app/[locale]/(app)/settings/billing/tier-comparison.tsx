"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { featureLabel } from "@/lib/feature-labels";
import type { TierSummary } from "@/types/settings";

/** Free / Premium / Pro side by side, built from the backend's own limit and feature tables so
 * the page cannot promise something the enforcement code does not know about. */
export function TierComparison({
  tiers,
  current,
}: {
  tiers: TierSummary[];
  current: string;
}): ReactNode {
  const t = useTranslations("settings.billing.compare");
  const tUsage = useTranslations("settings.usage");
  if (tiers.length === 0) {
    return null;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {tiers.map((tier) => {
        const isCurrent = tier.tier === current;
        return (
          <section
            key={tier.tier}
            aria-label={tUsage(`tierOption.${tier.tier}`)}
            className={cn(
              "flex flex-col gap-3 rounded-[var(--radius-card)] border p-4",
              isCurrent ? "border-[var(--accent)]" : "border-[var(--border)]",
            )}
          >
            <header className="flex items-center justify-between gap-2">
              <h3 className="font-medium text-[15px] text-[var(--text)]">
                {tUsage(`tierOption.${tier.tier}`)}
              </h3>
              {isCurrent ? (
                <span className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--accent-text)]">
                  {t("current")}
                </span>
              ) : null}
            </header>
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-[13px] text-[var(--text)]">
              <li>
                {tier.chat_limit_per_day === null
                  ? t("chatsUnlimited")
                  : t("chatsPerDay", { count: tier.chat_limit_per_day })}
              </li>
              <li className={tier.sponsor_data ? undefined : "text-[var(--text-muted)]"}>
                {tier.sponsor_data ? t("sponsorData") : t("noSponsorData")}
              </li>
              {tier.features.includes("deep_research") ? <li>{t("deepResearch")}</li> : null}
            </ul>
            {tier.features.filter((f) => f !== "deep_research").length > 0 ? (
              <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                {tier.features
                  .filter((f) => f !== "deep_research")
                  .map((f) => (
                    <li
                      key={f}
                      className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]"
                    >
                      {featureLabel(tUsage, f)}
                    </li>
                  ))}
              </ul>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
