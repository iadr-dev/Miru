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

/** ETF 股息日曆 tab — the SAME whole-market `get_dividend_calendar` query and
 * `DividendCalendarTable` the TW market tab's 股息日曆 sub-tab already uses
 * (`components/market/dividend-calendar-panel.tsx`), filtered client-side to this one symbol —
 * the backend tool has no per-symbol input (it's whole-market by design, see its own docstring),
 * so this is the same "fetch whole, filter locally" shape every other per-symbol slice of a
 * whole-market dataset in this codebase already uses. Not a fork: same query key family, same
 * table component, only the events array is narrowed before render. */
export function EtfDividendCalendarPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
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
          const events = data.events.filter((event) => event.symbol === symbol);
          return (
            <DividendCalendarTable
              key={`${cursor.year}-${cursor.month}`}
              year={data.year}
              month={data.month}
              events={events}
              onPrevMonth={goToPrevMonth}
              onNextMonth={goToNextMonth}
            />
          );
        }}
      </QueryState>
    </section>
  );
}
