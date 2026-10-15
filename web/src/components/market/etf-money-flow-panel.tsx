"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EtfMoneyFlowTable } from "@/components/market/etf-money-flow-table";
import { QueryState } from "@/components/ui/query-state";
import { getEtfMoneyFlow } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

type FlowDirection = "inflow" | "outflow";

/** A bounded 5-trading-day window ending today — wide enough for a meaningful cross-ETF
 * consensus without approaching `GetEtfMoneyFlowInput`'s own 30-day cap. */
function recentWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 5);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

/** Tab-panel wrapper for the ETF tab's 資金持股 sub-tab — owns the single `/market/etf-money-flow`
 * query and the inflow/outflow ranking toggle. Renders its own `<section>` card shell (title +
 * subtitle + toggle in the header, padded body) matching every other market card's own shell
 * (`LeaderboardCard`, `MarginMaintenanceCard`) — `RainbowBorderCard` only supplies the animated
 * ring, never padding/typography, so a caller that skips its own `<section>` renders content
 * flush against the border with no title, which this used to do. Sponsor-tier: an unentitled
 * caller gets the `{locked, required_tier, preview}` envelope at HTTP 200 (never an error), which
 * this wraps in `SponsorTeaser` exactly like `TopSplit`/`BreadthOrderBookCard` already do — a
 * plain shaped skeleton `<div>` as the blurred child, never a live component fed empty data. */
export function EtfMoneyFlowPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.etfMoneyFlow");
  const [direction, setDirection] = useState<FlowDirection>("inflow");
  const flowWindow = recentWindow();

  const query = useQuery({
    queryKey: queryKeys.marketRead(
      "etf_money_flow",
      market,
      flowWindow.startDate,
      flowWindow.endDate,
    ),
    queryFn: () => getEtfMoneyFlow(market, flowWindow.startDate, flowWindow.endDate),
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={560} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <>
                <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
                <p className="mt-3 text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("unavailable")}
                </p>
              </>
            );
          }
          const rows = direction === "inflow" ? data.inflow_ranking : data.outflow_ranking;
          return (
            <>
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
                  <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
                    {t("totalCount", { count: data.total_stock_count })}
                    {data.holding_as_of ? (
                      <>
                        {" "}
                        · {t("asOf", { date: data.holding_as_of })} · {t("nextUpdate")}
                      </>
                    ) : null}
                  </p>
                </div>
                <div className="inline-flex shrink-0 gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]">
                  {(["inflow", "outflow"] as const).map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDirection(d)}
                      aria-pressed={direction === d}
                      className={cn(
                        "rounded-[var(--radius-pill)] px-3.5 py-1 text-[12px] transition-colors",
                        direction === d
                          ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                          : "text-[var(--text-muted)] hover:text-[var(--text)]",
                      )}
                    >
                      {t(d === "inflow" ? "inflow" : "outflow")}
                    </button>
                  ))}
                </div>
              </div>
              <EtfMoneyFlowTable rows={rows} market={market} />
            </>
          );
        }}
      </QueryState>
    </section>
  );
}
