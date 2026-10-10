"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getFundProfile } from "@/lib/api/etf-detail";
import type { Market } from "@/types/settings";

/** 總覽 tab's fund-info card — the ETF-equivalent of `ProfileCard` (basic-info-panel.tsx), which
 * only has fields a plain equity has (chairman/EPS/capital, all `None` for a fund). yfinance's
 * `.info` carries real fund-specific fields nothing on this page surfaced before
 * (`get_fund_profile`/`Capability.FUND_PROFILE`): AUM, category, issuer, YTD return,
 * distribution yield, inception date. Renders nothing (not an empty-state card) when the
 * symbol has none of these — `UsStockOverviewPanel`'s existing `ProfileCard`/`EsgCard` already
 * cover the "no data for this symbol" case, this card only adds to that when there's real fund
 * data to show. */
export function FundProfileCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.fundProfile");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["fund_profile", symbol, market],
    queryFn: () => getFundProfile(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5 xl:col-span-2">
        <CardSkeletonBody height={120} />
      </section>
    );
  }
  const profile = query.data?.profile;
  if (!profile) return null;

  const stats: [string, string, string][] = [
    [t("aum"), formatCompactNumber(profile.total_assets ?? null, locale), "var(--chart-1)"],
    [
      t("ytdReturn"),
      profile.ytd_return_pct != null
        ? `${formatNumber(profile.ytd_return_pct, locale, { decimals: 2 })}%`
        : "—",
      "var(--chart-2)",
    ],
    [
      t("distributionYield"),
      profile.distribution_yield_pct != null
        ? `${formatNumber(profile.distribution_yield_pct, locale, { decimals: 2 })}%`
        : "—",
      "var(--chart-3)",
    ],
  ];
  const identity: [string, string][] = [
    [t("category"), profile.category ?? "—"],
    [t("fundFamily"), profile.fund_family ?? "—"],
    [t("inceptionDate"), profile.inception_date ?? "—"],
  ];

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <div className="grid grid-cols-3 gap-3">
        {stats.map(([label, value, color]) => (
          <div key={label}>
            <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
            <p className="font-num text-[16px] tabular-nums" style={{ color }}>
              {value}
            </p>
          </div>
        ))}
      </div>
      <dl className="mt-4 grid grid-cols-1 divide-y divide-[var(--border)] sm:grid-cols-3 sm:divide-y-0">
        {identity.map(([label, value]) => (
          <div
            key={label}
            className="flex items-center justify-between py-1.5 sm:flex-col sm:items-start sm:gap-0.5 sm:px-2"
          >
            <dt className="text-[12px] text-[var(--text-muted)]">{label}</dt>
            <dd className="text-[13px] text-[var(--text)]">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
