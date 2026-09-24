"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { DividendCalendarCard } from "@/components/etf/dividend-calendar-card";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getEtfExpenseRatio, getEtfFundInfo, getEtfThemeTags } from "@/lib/api/etf-detail";
import { getTechnicalIndicators } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const DAYS_PER_YEAR = 365.25;

/** 年化報酬(成立以來) — `(latest close / inception-day close) ^ (1/years) - 1`, over the full
 * `inception_date`-to-today window from `get_technical_indicators`'s plain OHLC bars (no
 * per-symbol range cap, unlike `get_price_history`'s 60-day bound — the only already-live
 * capability that can span years back to a fund's inception). Genuinely `None` (never
 * fabricated) when `inception_date` is missing or fewer than 2 bars exist in that window. */
function useAnnualizedReturnSinceInception(
  symbol: string,
  market: Market,
  inceptionDate: string | null | undefined,
): { value: number | null; isLoading: boolean } {
  const query = useQuery({
    queryKey: ["etf_inception_return", symbol, market, inceptionDate],
    queryFn: () => getTechnicalIndicators(symbol, market, { startDate: inceptionDate as string }),
    enabled: !!inceptionDate,
  });
  if (!inceptionDate) return { value: null, isLoading: false };
  const bars = query.data?.bars ?? [];
  if (bars.length < 2) return { value: null, isLoading: query.isLoading };
  const first = bars[0];
  const last = bars.at(-1);
  if (!first || !last || first.close <= 0) return { value: null, isLoading: query.isLoading };
  const years =
    (new Date(last.trade_date).getTime() - new Date(first.trade_date).getTime()) /
    (DAYS_PER_YEAR * 24 * 60 * 60 * 1000);
  if (years <= 0) return { value: null, isLoading: query.isLoading };
  const totalReturn = last.close / first.close;
  const annualized = totalReturn ** (1 / years) - 1;
  return { value: annualized * 100, isLoading: query.isLoading };
}

/** 總覽 — fund-level reference (`get_etf_fund_info`/`get_etf_expense_ratio`, both public,
 * matching the reference app's 基本資料 key-value block: 資產規模/成立年數/內扣費用/基金名稱/經理人/
 * 投信公司/保管銀行) plus the reference app's own 年化報酬(成立以來) headline stat. No AUM/units-
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
  const annualizedReturn = useAnnualizedReturnSinceInception(symbol, market, info?.inception_date);
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
        <span className="text-[12.5px] text-[var(--text-muted)]">
          {t("annualizedReturnSinceInception")}
        </span>
        {annualizedReturn.isLoading ? (
          <div className="mt-2 h-8 w-32 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        ) : annualizedReturn.value != null ? (
          <div
            className="mt-1 font-num text-[28px] tabular-nums"
            style={{ color: annualizedReturn.value >= 0 ? "var(--up)" : "var(--down)" }}
          >
            {annualizedReturn.value >= 0 ? "▲" : "▼"}
            {formatNumber(Math.abs(annualizedReturn.value), locale, { decimals: 1 })}%
          </div>
        ) : (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            {t("annualizedReturnUnavailable")}
          </p>
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
