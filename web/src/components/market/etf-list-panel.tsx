"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { EtfListTable } from "@/components/market/etf-list-table";
import { getEtfList } from "@/lib/api/market";
import type { Market } from "@/types/settings";

/** Tab-panel wrapper for the ETF tab — owns the single `/market/etf-list` query and hands the
 * response straight to `EtfListTable`. No local sort/filter state here; the table owns that
 * itself, so a re-sort click never triggers a new request. */
export function EtfListPanel({ market }: { market: Market }): ReactNode {
  const t = useTranslations("market.cards.etfList");
  const query = useQuery({
    queryKey: ["etf_list", market],
    queryFn: () => getEtfList(market),
  });

  return (
    <QueryState query={query}>
      {(data) => {
        if (!data.data_available) {
          return (
            <p className="text-[13px] text-[var(--text-muted)]">
              {data.unavailable_reason ?? t("unavailable")}
            </p>
          );
        }
        return (
          <EtfListTable
            rows={data.rows}
            currentnessCaveat={data.currentness_caveat}
            aumCaveat={data.aum_caveat}
            expenseRatioCaveat={data.expense_ratio_caveat}
            distributionCaveat={data.distribution_caveat}
          />
        );
      }}
    </QueryState>
  );
}
