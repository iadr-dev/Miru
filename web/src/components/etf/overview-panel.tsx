"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getEtfExpenseRatio, getEtfFundInfo } from "@/lib/api/etf-detail";
import type { Market } from "@/types/settings";

/** 總覽 — fund-level reference (`get_etf_fund_info`/`get_etf_expense_ratio`, both public,
 * matching the reference app's 基本資料 key-value block: 資產規模/成立年數/內扣費用/基金名稱/經理人/
 * 投信公司/保管銀行). No AUM/units-history chart here — that lives in the 折溢價 tab, which is the
 * dataset that actually carries a daily series; this tab is a point-in-time fund profile. */
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

  if (fundInfoQuery.isLoading || expenseQuery.isLoading) return <CardSkeletonBody height={360} />;

  const info = fundInfoQuery.data?.info;
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
          ? formatNumber(info.units_outstanding, locale, { decimals: 0 })
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
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
  );
}
