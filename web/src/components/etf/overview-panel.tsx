"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { DividendCalendarCard } from "@/components/etf/dividend-calendar-card";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  getEtfExpenseRatio,
  getEtfFundInfo,
  getEtfThemeTags,
  getFundTotalReturn,
} from "@/lib/api/etf-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

/** 今年以來報酬 — `get_fund_comparison`'s dividend-reinvested total return (see that tool's own
 * module docstring for the reinvestment convention) over a calendar-year-to-date window
 * (Jan 1 of the current year through today), computed via a live `lookback_days` derived from
 * today's own date. Deliberately YTD, not since-inception: a since-inception window can span a
 * stock split (confirmed live for 0050 — an unadjusted 2025-06-18 4-for-1 split makes any
 * multi-year raw-close total-return calc read as a fabricated ~-75% crash at that date, since
 * this codebase's price pipeline has no split-adjustment step anywhere). A YTD window is short
 * enough that it essentially never crosses a split, so this sidesteps that gap entirely rather
 * than risk showing a distorted number. Genuinely `None` (never fabricated) when the tool itself
 * couldn't compute a return (no usable price history in the window). */
function useYtdReturn(
  symbol: string,
  market: Market,
): { value: number | null; isLoading: boolean } {
  const lookbackDays = Math.ceil(
    (Date.now() - new Date(new Date().getFullYear(), 0, 1).getTime()) / (24 * 60 * 60 * 1000) + 1,
  );
  const query = useQuery({
    queryKey: ["fund_total_return_ytd", symbol, market, lookbackDays],
    queryFn: () => getFundTotalReturn(symbol, market, lookbackDays),
  });
  const row = query.data?.rows[0];
  const totalReturnPct = row?.total_return_pct;
  return { value: totalReturnPct ?? null, isLoading: query.isLoading };
}

/** 總覽 — fund-level reference (`get_etf_fund_info`/`get_etf_expense_ratio`, both public,
 * matching the reference app's 基本資料 key-value block: 資產規模/成立年數/內扣費用/基金名稱/經理人/
 * 投信公司/保管銀行) plus the reference app's own 今年以來報酬 headline stat. No AUM/units-
 * history chart here — that lives in the 折溢價 tab, which is the dataset that actually carries a
 * daily series; this tab is a point-in-time fund profile. */
export function OverviewPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.overview");
  const locale = useLocale();
  const fundInfoQuery = useQuery({
    queryKey: ["etf_fund_info", symbol, market],
    queryFn: () => getEtfFundInfo(symbol, market),
  });
  const expenseQuery = useQuery({
    queryKey: ["etf_expense_ratio", symbol, market],
    queryFn: () => getEtfExpenseRatio(symbol, market),
  });
  const themeTagsQuery = useQuery({
    queryKey: ["etf_theme_tags", symbol, market],
    queryFn: () => getEtfThemeTags(symbol, market),
  });
  const info = fundInfoQuery.data?.info;
  const ytdReturn = useYtdReturn(symbol, market);
  const holderCountWan = themeTagsQuery.data?.holder_count_wan;

  if (fundInfoQuery.isLoading || expenseQuery.isLoading) return <CardSkeletonBody height={360} />;

  const expense = expenseQuery.data?.info;

  if (!info) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>
      </section>
    );
  }

  const rows: Array<{ label: string; value: string | null }> = [
    { label: t("field.fundType"), value: info.fund_type },
    { label: t("field.trackedIndex"), value: info.tracked_index ?? null },
    {
      label: t("field.expenseRatio"),
      value:
        expense?.total_expense_ratio_pct_approx != null
          ? `${formatNumber(expense.total_expense_ratio_pct_approx, locale, { decimals: 2 })}%`
          : null,
    },
    {
      label: t("field.unitsOutstanding"),
      value:
        info.units_outstanding != null
          ? t("field.unitsOutstandingValue", {
              value: formatNumber(info.units_outstanding, locale, { decimals: 0 }),
            })
          : null,
    },
    { label: t("field.listedDate"), value: info.listed_date ?? null },
    { label: t("field.inceptionDate"), value: info.inception_date ?? null },
    { label: t("field.fundName"), value: info.fund_name },
    { label: t("field.managerName"), value: info.manager_name ?? null },
    { label: t("field.managerCompany"), value: info.manager_company ?? null },
    {
      label: t("field.custodianBank"),
      value: info.custodian_bank ?? expense?.custodian_bank ?? null,
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <section
        className={cn(
          "glass rounded-[var(--radius-card)] border border-[var(--border)] p-5",
          "bg-[color-mix(in_srgb,var(--up)_10%,var(--surface)_90%)] backdrop-blur-[16px] backdrop-saturate-150",
        )}
      >
        <span className="text-[12.5px] text-[var(--text-muted)]">{t("ytdReturn")}</span>
        {ytdReturn.isLoading ? (
          <div className="mt-2 h-8 w-32 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        ) : ytdReturn.value != null ? (
          <div
            className="mt-1 font-num text-[28px] tabular-nums"
            style={{ color: ytdReturn.value >= 0 ? "var(--up)" : "var(--down)" }}
          >
            {ytdReturn.value >= 0 ? "▲" : "▼"}
            {formatNumber(Math.abs(ytdReturn.value), locale, { decimals: 1 })}%
          </div>
        ) : (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("ytdReturnUnavailable")}</p>
        )}
      </section>

      <section
        className={cn(
          "glass rounded-[var(--radius-card)] border border-[var(--border)] p-5",
          "bg-[color-mix(in_srgb,var(--accent)_10%,var(--surface)_90%)] backdrop-blur-[16px] backdrop-saturate-150",
        )}
      >
        <span className="text-[12.5px] text-[var(--text-muted)]">{t("holderCount")}</span>
        {themeTagsQuery.isLoading ? (
          <div className="mt-2 h-8 w-32 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        ) : holderCountWan != null ? (
          <div className="mt-1 font-num text-[28px] text-[var(--text)] tabular-nums">
            {formatNumber(holderCountWan, locale, { decimals: 2 })}
            <span className="ml-1 text-[15px] text-[var(--text-muted)]">
              {t("holderCountUnit")}
            </span>
          </div>
        ) : (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("holderCountUnavailable")}</p>
        )}
      </section>

      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <dl className="flex flex-col gap-3">
          {rows.map(({ label, value }) => (
            <div key={label} className="flex items-baseline justify-between gap-3">
              <dt className="text-[12.5px] text-[var(--text-muted)]">{label}</dt>
              <dd className="text-right text-[13px] text-[var(--text)]">{value ?? "—"}</dd>
            </div>
          ))}
        </dl>
        {expense?.total_expense_ratio_pct_approx != null ? (
          <p className="mt-4 text-[11px] text-[var(--text-muted)] leading-[1.5]">
            {t("expenseFloorCaveat")}
          </p>
        ) : null}
      </section>

      <div className="xl:col-span-2">
        <DividendCalendarCard symbol={symbol} market={market} />
      </div>
    </div>
  );
}
