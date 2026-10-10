"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { MonthGridCalendar } from "@/components/ui/month-grid-calendar";
import { ViewToggleButton } from "@/components/ui/view-toggle-button";
import { getCalendarEventsFeed } from "@/lib/api/stock-detail";
import type { Market } from "@/types/settings";
import type { EarningsCalendarRow } from "@/types/stock-detail";

const EVENT_DOT_COLOR = "var(--chart-1)";

type CalendarView = "list" | "calendar";

/** US 總覽's upcoming-events calendar card — `get_calendar_events_feed` is a market-wide feed
 * (`yfinance.Calendars`, no per-symbol input), so this fetches the whole earnings-calendar slice
 * and filters client-side to this symbol's own rows, the same "fetch whole small feed, filter
 * locally" shape `AssetHeader`'s ETF-list lookup already uses. IPO/economic rows are dropped —
 * they're market-wide noise not scoped to this stock, unlike its own earnings date. Reuses the
 * exact 清單/日曆 + `MonthGridCalendar` pattern `basic-info-panel.tsx`'s `AnnouncementsCard` and
 * `research-charts-panel.tsx`'s `StockEventsCard` already established, so every calendar-shaped
 * card in this app looks and behaves the same. */
export function UsMarketCalendarCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.overview");
  const [view, setView] = useState<CalendarView>("list");
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() + 1 });

  const query = useQuery({
    queryKey: ["calendar_events_feed", market],
    queryFn: () => getCalendarEventsFeed(market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5 xl:col-span-2">
        <CardSkeletonBody height={220} />
      </section>
    );
  }
  const data = query.data;
  if (!data) return null;

  const events = data.earnings
    .filter((row) => row.symbol === symbol)
    .sort((a, b) => a.event_start.localeCompare(b.event_start));

  let body: ReactNode;
  if (events.length === 0) {
    body = <p className="text-[13px] text-[var(--text-muted)]">{t("calendarEmpty")}</p>;
  } else if (view === "list") {
    body = <EarningsTimeline events={events} />;
  } else {
    body = (
      <EarningsCalendarView
        year={cursor.year}
        month={cursor.month}
        events={events}
        onPrevMonth={() =>
          setCursor(({ year, month }) =>
            month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 },
          )
        }
        onNextMonth={() =>
          setCursor(({ year, month }) =>
            month === 12 ? { year: year + 1, month: 1 } : { year, month: month + 1 },
          )
        }
      />
    );
  }

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("calendarTitle")}</h2>
        {events.length > 0 ? (
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
        ) : null}
      </div>
      {body}
    </section>
  );
}

function EarningsTimeline({
  events,
}: {
  readonly events: readonly EarningsCalendarRow[];
}): ReactNode {
  const t = useTranslations("usStock.overview");
  return (
    <ol className="divide-y divide-[var(--border)]">
      {events.map((event) => (
        <li key={event.event_start} className="flex items-center gap-3 py-2.5">
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: EVENT_DOT_COLOR }}
            aria-hidden
          />
          <span className="flex-1 text-[13px] text-[var(--text)]">{t("earningsEventLabel")}</span>
          {event.timing ? (
            <span
              className="rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]"
              style={{
                backgroundColor: "color-mix(in srgb, var(--chart-1) 16%, transparent)",
                color: EVENT_DOT_COLOR,
              }}
            >
              {event.timing}
            </span>
          ) : null}
          <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
            {event.event_start.slice(0, 10)}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Same left-list / right-grid two-column arrangement `StockEventsCalendarView`
 * (`research-charts-panel.tsx`) uses over the shared `MonthGridCalendar`. */
function EarningsCalendarView({
  year,
  month,
  events,
  onPrevMonth,
  onNextMonth,
}: {
  readonly year: number;
  readonly month: number;
  readonly events: readonly EarningsCalendarRow[];
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("usStock.overview");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const dotsByDay = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const event of events) {
      const day = event.event_start.slice(0, 10);
      map.set(day, [EVENT_DOT_COLOR]);
    }
    return map;
  }, [events]);

  const visibleEvents = selectedDay
    ? events.filter((event) => event.event_start.slice(0, 10) === selectedDay)
    : events;

  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-start">
      <div className="min-w-0 md:w-64 md:shrink-0">
        {visibleEvents.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("calendarEmpty")}</p>
        ) : (
          <EarningsTimeline events={visibleEvents} />
        )}
      </div>

      <div className="min-w-0 flex-1">
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
      </div>
    </div>
  );
}
