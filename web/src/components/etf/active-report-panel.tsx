"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { getActiveEtfReport } from "@/lib/api/etf-detail";
import { cn } from "@/lib/cn";
import type { ActiveEtfReportData, HoldingStatus } from "@/types/etf-detail";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

// `HoldingStatus`'s own zh-TW-string values (新增/刪除/加碼/減碼) are already the display label —
// only the color mapping is added here, categorical (`--chart-N`), never signed 紅漲綠跌 (a
// holding-status bucket is a classification, not a price/score direction).
const STATUS_COLOR: Record<HoldingStatus, string> = {
  新增: "var(--chart-1)",
  刪除: "var(--text-muted)",
  加碼: "var(--chart-2)",
  減碼: "var(--chart-3)",
  no_prior_data: "var(--text-muted)",
};

export function ActiveReportPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.activeReport");
  const query = useQuery({
    queryKey: ["active_etf_report", symbol, market],
    queryFn: () => getActiveEtfReport(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={420} />
      </section>
    );
  }
  if (!query.data) return null;

  if (isMaskedTeaser(query.data)) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <SponsorTeaser
          headline={t("title")}
          preview={query.data.preview}
          requiredTier={query.data.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </section>
    );
  }

  return <ActiveReportBody data={query.data} />;
}

/** Shared body — also used by `holdings-panel.tsx`'s active-management-ETF branch so the two
 * tabs (操作日報/成分股) never re-derive the same rows-fetch or table shape twice. Two separate
 * cards (summary / holdings table), matching the stock-detail page's own per-block card
 * convention (e.g. `basic-info-panel.tsx`) rather than stacking everything into one card. */
export function ActiveReportBody({ data }: { readonly data: ActiveEtfReportData }): ReactNode {
  const t = useTranslations("etf.activeReport");

  if (data.report_date == null) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      </section>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <ActiveReportSummaryCard data={{ ...data, report_date: data.report_date }} t={t} />
      <ActiveReportHoldingsCard data={data} t={t} />
    </div>
  );
}

function ActiveReportSummaryCard({
  data,
  t,
}: {
  readonly data: ActiveEtfReportData & { report_date: string };
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  const counts = {
    新增: data.rows.filter((row) => row.status === "新增").length,
    刪除: data.rows.filter((row) => row.status === "刪除").length,
    加碼: data.rows.filter((row) => row.status === "加碼").length,
    減碼: data.rows.filter((row) => row.status === "減碼").length,
  };

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <span className="text-[11px] text-[var(--text-muted)]">
          {t("asOfDate", { date: data.report_date })}
        </span>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3">
        <StatCard
          label={t("fundSize")}
          value={formatCompactNumber(data.fund_size ?? null, locale)}
          delta={data.fund_size_change_pct}
          locale={locale}
        />
        <StatCard
          label={t("estimatedPremiumDiscount")}
          value={
            data.estimated_premium_discount_pct != null
              ? `${formatNumber(data.estimated_premium_discount_pct, locale, { decimals: 2 })}%`
              : "—"
          }
          delta={null}
          locale={locale}
        />
      </div>

      <div className="grid grid-cols-4 gap-3">
        {(["新增", "刪除", "加碼", "減碼"] as const).map((status) => (
          <div
            key={status}
            className="flex flex-col items-center gap-1 rounded-[var(--radius-input)] border p-3"
            style={{
              borderColor: `color-mix(in srgb, ${STATUS_COLOR[status]} 35%, var(--border))`,
              backgroundColor: `color-mix(in srgb, ${STATUS_COLOR[status]} 6%, var(--surface-2))`,
            }}
          >
            <span className="text-[12px] text-[var(--text-muted)]">{status}</span>
            <span
              className="font-num text-[20px] tabular-nums"
              style={{ color: STATUS_COLOR[status] }}
            >
              {counts[status]}
              {t("countUnit")}
            </span>
          </div>
        ))}
      </div>

      {data.fund_size_caveat ? (
        <p className="mt-3 text-[11px] text-[var(--text-muted)] leading-[1.5]">
          {data.fund_size_caveat}
        </p>
      ) : null}
    </section>
  );
}

function ActiveReportHoldingsCard({
  data,
  t,
}: {
  readonly data: ActiveEtfReportData;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("holdingsTitle")}</h2>
      {data.rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("noChanges")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
                <th className="py-2 text-left font-medium">{t("column.stock")}</th>
                <th className="py-2 text-left font-medium">{t("column.status")}</th>
                <th className="py-2 text-right font-medium">{t("column.sharesDelta")}</th>
                <th className="py-2 text-right font-medium">{t("column.currentWeight")}</th>
                <th className="py-2 text-right font-medium">{t("column.weightDelta")}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.component_stock_id} className="border-[var(--border)] border-b">
                  <td className="py-2 text-[var(--text)]">
                    {row.component_stock_name}
                    <span className="ml-1 font-num text-[11px] text-[var(--text-muted)]">
                      {row.component_stock_id}
                    </span>
                  </td>
                  <td className="py-2" style={{ color: STATUS_COLOR[row.status] }}>
                    {row.status}
                  </td>
                  <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(row.shares_delta, locale, { decimals: 0 })}
                  </td>
                  <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
                    {row.current_weight != null
                      ? `${formatNumber(row.current_weight, locale, { decimals: 2 })}%`
                      : "—"}
                  </td>
                  <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
                    {row.weight_delta_pct != null
                      ? `${formatNumber(row.weight_delta_pct, locale, { decimals: 2 })}%`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function StatCard({
  label,
  value,
  delta,
  locale,
}: {
  readonly label: string;
  readonly value: string;
  readonly delta: number | null | undefined;
  readonly locale: string;
}): ReactNode {
  const isUp = delta != null && delta >= 0;
  return (
    <div className="flex flex-col gap-1 rounded-[var(--radius-input)] border border-[var(--border)] p-3">
      <span className="text-[12px] text-[var(--text-muted)]">{label}</span>
      <span className="font-num text-[18px] text-[var(--text)] tabular-nums">{value}</span>
      {delta != null ? (
        <span
          className={cn("font-num text-[12px] tabular-nums")}
          style={{ color: isUp ? "var(--up)" : "var(--down)" }}
        >
          {isUp ? "▲" : "▼"}
          {formatNumber(Math.abs(delta), locale, { decimals: 2 })}%
        </span>
      ) : null}
    </div>
  );
}
