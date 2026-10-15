"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolLink } from "@/components/market/symbol-link";
import { QueryState } from "@/components/ui/query-state";
import { queryKeys } from "@/lib/api/query-keys";
import { getCalendarEventsFeed } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import type { EarningsCalendarRow } from "@/types/stock/events";

const EVENT_DOT_COLOR = "var(--chart-1)";
const US_MARKET: Market = "US";
const COLLAPSED_ROW_COUNT = 10;

/** 每日焦點's US-only earnings-calendar card — fills the grid cell 重大訊息 (MOPS-only) occupies
 * for TW. Market-wide variant of `us-stock/calendar-events-card.tsx`'s `UsMarketCalendarCard`:
 * that component fetches the SAME `get_calendar_events_feed` market-wide feed and filters
 * client-side to one symbol for the stock-detail page; this renders the whole feed's soonest
 * upcoming events across every symbol instead (same `queryKey`, so TanStack dedupes the request
 * if both components are ever mounted at once). List-only — no month-grid toggle, since this is a
 * "what's coming up market-wide" glance, not a per-symbol calendar. */
export function EarningsCalendarPanel(): ReactNode {
  const t = useTranslations("market.cards.earningsCalendar");
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: queryKeys.read("calendar_events_feed", US_MARKET),
    queryFn: () => getCalendarEventsFeed(US_MARKET),
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={380} />}>
        {(data) => {
          const events = [...data.earnings].sort((a, b) =>
            a.event_start.localeCompare(b.event_start),
          );
          if (events.length === 0) {
            return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
          }
          const visible = expanded ? events : events.slice(0, COLLAPSED_ROW_COUNT);
          return (
            <>
              <EarningsList events={visible} />
              {!expanded && events.length > COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: events.length })}</span>
                </button>
              ) : null}
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

/** Groups events by calendar month (`event_start`'s own `YYYY-MM`) — a market-wide feed spanning
 * many weeks reads better broken into 月 sections (e.g. "2026年9月") than one flat list, the same
 * way a monthly earnings calendar is conventionally presented. Order is preserved from the
 * caller's own sort (ascending `event_start`), so each group's insertion order stays chronological
 * without a second sort pass here. */
function groupEventsByMonth(
  events: readonly EarningsCalendarRow[],
): ReadonlyMap<string, EarningsCalendarRow[]> {
  const groups = new Map<string, EarningsCalendarRow[]>();
  for (const event of events) {
    const monthKey = event.event_start.slice(0, 7);
    const group = groups.get(monthKey);
    if (group) {
      group.push(event);
    } else {
      groups.set(monthKey, [event]);
    }
  }
  return groups;
}

function EarningsList({ events }: { readonly events: readonly EarningsCalendarRow[] }): ReactNode {
  const t = useTranslations("market.cards.earningsCalendar");
  const groups = groupEventsByMonth(events);

  return (
    <div className="flex flex-col gap-4">
      {[...groups.entries()].map(([monthKey, monthEvents]) => {
        const [yearStr, monthStr] = monthKey.split("-");
        return (
          <div key={monthKey}>
            <h3 className="mb-1 text-[12px] text-[var(--text-muted)]">
              {t("monthHeading", { year: Number(yearStr), month: Number(monthStr) })}
            </h3>
            <ol className="divide-y divide-[var(--border)]">
              {monthEvents.map((event) => (
                <li
                  key={`${event.symbol}_${event.event_start}`}
                  className="flex items-center gap-3 py-2.5"
                >
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: EVENT_DOT_COLOR }}
                    aria-hidden
                  />
                  <SymbolLink
                    symbol={event.symbol}
                    market={US_MARKET}
                    className="flex-1 truncate text-[13px] text-[var(--text)] hover:underline"
                  >
                    {event.company}
                    <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">
                      {event.symbol}
                    </span>
                  </SymbolLink>
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
          </div>
        );
      })}
    </div>
  );
}
