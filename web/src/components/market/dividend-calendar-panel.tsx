"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { DividendCalendarTable } from "@/components/market/dividend-calendar-table";
import { getDividendCalendar } from "@/lib/api/market";
import type { Market } from "@/types/settings";

const DECEMBER = 12;

/** Tab-panel wrapper for the ETF tab's 股息日曆 sub-tab — owns the current year/month cursor
 * (local `useState`, not the shared `useMarketTab` store: nothing outside this subtree needs to
 * know which month is being viewed) and the single `/market/dividend-calendar` query keyed by
 * that cursor, so navigating months triggers a real re-fetch rather than filtering an
 * already-fetched whole-history response client-side (mirrors `EtfMoneyFlowPanel`'s own
 * per-window query-key pattern). `DividendCalendarTable` is keyed by `${year}-${month}` so its
 * own day-selection state resets when the visible month changes — otherwise a day selected in
 * August would silently carry over as a (meaningless) filter into September. */
export function DividendCalendarPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.dividendCalendar");
  const today = new Date();
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() + 1 });

  const query = useQuery({
    queryKey: ["dividend_calendar", market, cursor.year, cursor.month],
    queryFn: () => getDividendCalendar(market, cursor.year, cursor.month),
  });

  const goToPrevMonth = (): void => {
    setCursor((prev) =>
      prev.month === 1
        ? { year: prev.year - 1, month: DECEMBER }
        : { year: prev.year, month: prev.month - 1 },
    );
  };
  const goToNextMonth = (): void => {
    setCursor((prev) =>
      prev.month === DECEMBER
        ? { year: prev.year + 1, month: 1 }
        : { year: prev.year, month: prev.month + 1 },
    );
  };

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={480} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <p className="text-[13px] text-[var(--text-muted)]">
                {data.unavailable_reason ?? t("unavailable")}
              </p>
            );
          }
          return (
            <DividendCalendarTable
              key={`${cursor.year}-${cursor.month}`}
              year={data.year}
              month={data.month}
              events={data.events}
              onPrevMonth={goToPrevMonth}
              onNextMonth={goToNextMonth}
            />
          );
        }}
      </QueryState>
    </section>
  );
}
