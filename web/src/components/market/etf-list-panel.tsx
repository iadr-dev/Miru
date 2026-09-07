"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EtfListTable } from "@/components/market/etf-list-table";
import { getEtfList } from "@/lib/api/market";
import type { Market } from "@/types/settings";

/** Tab-panel wrapper for the ETF tab — owns the single `/market/etf-list` query, renders the
 * card's own title + 資料日期/下次更新 header (this tab previously had no header at all —
 * `EtfListTable` is table body only), and hands the response to `EtfListTable`. No local sort/
 * filter state here; the table owns that itself, so a re-sort click never triggers a new
 * request. */
export function EtfListPanel({ market }: { market: Market }): ReactNode {
  const t = useTranslations("market.cards.etfList");
  const query = useQuery({
    queryKey: ["etf_list", market],
    queryFn: () => getEtfList(market),
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={700} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <>
                <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
                <p className="mt-3 text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("unavailable")}
                </p>
              </>
            );
          }
          return (
            <>
              <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">
                {t("title")}
                {data.trade_date ? (
                  <span className="ml-2 font-normal text-[12px] text-[var(--text-muted)]">
                    {t("asOf", { date: data.trade_date })} · {t("nextUpdate")}
                  </span>
                ) : null}
              </h2>
              <EtfListTable rows={data.rows} />
            </>
          );
        }}
      </QueryState>
    </section>
  );
}
