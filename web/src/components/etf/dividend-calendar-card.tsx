"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ExpandModal } from "@/components/ui/expand-modal";
import { MonthGridCalendar } from "@/components/ui/month-grid-calendar";
import { ViewToggleButton } from "@/components/ui/view-toggle-button";
import { getDividendCalendar } from "@/lib/api/market";
import type { DividendCalendarEventRow } from "@/types/market";
import type { Market } from "@/types/settings";

const DECEMBER = 12;
const COLLAPSED_EVENT_COUNT = 5;

// Same 3 categorical date-type colors `dividend-calendar-table.tsx` uses (除息日/最後購買日/
// 股利發放日) — never --up/--down, this is a date-type classification, not a signal.
const DOT_COLOR = {
  ex: "var(--chart-1)",
  lastBuy: "var(--chart-4)",
  payment: "var(--chart-3)",
} as const;

type CalendarView = "list" | "calendar";

/** ETF 總覽 tab's own dividend-calendar card — same list/calendar `ViewToggleButton` +
 * `MonthGridCalendar` shape the stock-detail page's 基本資料 `AnnouncementsCard` already uses,
 * just over `get_dividend_calendar` (whole-market, ETF-only) events filtered client-side to this
 * one symbol — the backend tool has no per-symbol input (see its own docstring), so this fetches
 * one month at a time and narrows locally, the same "fetch whole, filter locally" shape every
 * other per-symbol slice of a whole-market dataset in this codebase already uses. */
export function DividendCalendarCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.dividendCalendar");
  const [view, setView] = useState<CalendarView>("list");
  const [expanded, setExpanded] = useState(false);
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() + 1 };
  });

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

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={200} />
      </section>
    );
  }

  const data = query.data;
  const events = data?.data_available ? data.events.filter((event) => event.symbol === symbol) : [];

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
          <ViewToggleButton
            active={view === "list"}
            label={t("viewList")}
            onClick={() => setView("list")}
          />
          <ViewToggleButton
            active={view === "calendar"}
            label={t("viewCalendar")}
            onClick={() => setView("calendar")}
          />
        </div>
      </div>

      {!data?.data_available ? (
        <p className="text-[13px] text-[var(--text-muted)]">
          {data?.unavailable_reason ?? t("unavailable")}
        </p>
      ) : view === "list" ? (
        events.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
        ) : (
          <>
            <DividendEventList events={events.slice(0, COLLAPSED_EVENT_COUNT)} />
            {events.length > COLLAPSED_EVENT_COUNT ? (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
              >
                {t("expandTo", { count: events.length })}
              </button>
            ) : null}
            <ExpandModal
              open={expanded}
              title={t("title")}
              closeLabel={t("collapse")}
              onClose={() => setExpanded(false)}
            >
              <DividendEventList events={events} />
            </ExpandModal>
          </>
        )
      ) : (
        <DividendCalendarView
          year={cursor.year}
          month={cursor.month}
          events={events}
          onPrevMonth={goToPrevMonth}
          onNextMonth={goToNextMonth}
        />
      )}
    </section>
  );
}

function DividendCalendarView({
  year,
  month,
  events,
  onPrevMonth,
  onNextMonth,
}: {
  readonly year: number;
  readonly month: number;
  readonly events: readonly DividendCalendarEventRow[];
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("etf.dividendCalendar");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const dotsByDay = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const event of events) {
      const exColors = map.get(event.ex_dividend_date) ?? [];
      exColors.push(DOT_COLOR.ex);
      map.set(event.ex_dividend_date, exColors);
      const lastBuyColors = map.get(event.last_buy_date) ?? [];
      lastBuyColors.push(DOT_COLOR.lastBuy);
      map.set(event.last_buy_date, lastBuyColors);
      if (event.payment_date) {
        const paymentColors = map.get(event.payment_date) ?? [];
        paymentColors.push(DOT_COLOR.payment);
        map.set(event.payment_date, paymentColors);
      }
    }
    const deduped = new Map<string, string[]>();
    for (const [day, colors] of map) deduped.set(day, [...new Set(colors)]);
    return deduped;
  }, [events]);

  const visibleEvents = selectedDay
    ? events.filter(
        (e) =>
          e.ex_dividend_date === selectedDay ||
          e.last_buy_date === selectedDay ||
          e.payment_date === selectedDay,
      )
    : events;

  return (
    <div>
      <MonthGridCalendar
        year={year}
        month={month}
        dotsByDay={dotsByDay}
        selectedDay={selectedDay}
        onSelectDay={setSelectedDay}
        onPrevMonth={onPrevMonth}
        onNextMonth={onNextMonth}
        prevMonthLabel={t("prevMonth")}
        nextMonthLabel={t("nextMonth")}
        monthTitle={t("monthTitle", { year, month })}
      />

      <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted)]">
        <LegendDot color={DOT_COLOR.ex} label={t("legendExDate")} />
        <LegendDot color={DOT_COLOR.lastBuy} label={t("legendLastBuyDate")} />
        <LegendDot color={DOT_COLOR.payment} label={t("legendPaymentDate")} />
      </div>

      <DividendEventList events={visibleEvents} />
    </div>
  );
}

function LegendDot({
  color,
  label,
}: {
  readonly color: string;
  readonly label: string;
}): ReactNode {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2 rounded-full" style={{ backgroundColor: color }} aria-hidden />
      {label}
    </span>
  );
}

function DividendEventList({
  events,
}: {
  readonly events: readonly DividendCalendarEventRow[];
}): ReactNode {
  const t = useTranslations("etf.dividendCalendar");
  const locale = useLocale();

  if (events.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <ul className="mt-3 divide-y divide-[var(--border)]">
      {events.map((event) => (
        <li key={`${event.symbol}-${event.ex_dividend_date}`} className="py-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] text-[var(--text)]">{t("cashDividendLabel")}</span>
            <span className="text-right font-num text-[13px] text-[var(--text)] tabular-nums">
              {t("cashDividend", {
                amount: formatNumber(event.cash_dividend, locale, { decimals: 3 }),
              })}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-muted)]">
            <DateChip
              color={DOT_COLOR.ex}
              label={t("legendExDate")}
              date={event.ex_dividend_date}
            />
            <DateChip
              color={DOT_COLOR.lastBuy}
              label={t("legendLastBuyDate")}
              date={event.last_buy_date}
            />
            <DateChip
              color={DOT_COLOR.payment}
              label={t("legendPaymentDate")}
              date={event.payment_date ?? t("noPaymentDate")}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function DateChip({
  color,
  label,
  date,
}: {
  readonly color: string;
  readonly label: string;
  readonly date: string;
}): ReactNode {
  return (
    <span className="flex items-center gap-1">
      <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} aria-hidden />
      {label} {date}
    </span>
  );
}
