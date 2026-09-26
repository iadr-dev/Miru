"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolLink } from "@/components/market/symbol-link";
import { getCalendarEventsFeed } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import type { EarningsCalendarRow } from "@/types/stock-detail";

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
    queryKey: ["calendar_events_feed", US_MARKET],
    queryFn: () => getCalendarEventsFeed(US_MARKET),
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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

function EarningsList({ events }: { readonly events: readonly EarningsCalendarRow[] }): ReactNode {
  return (
    <ol className="divide-y divide-[var(--border)]">
      {events.map((event) => (
        <li key={`${event.symbol}_${event.event_start}`} className="flex items-center gap-3 py-2.5">
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
            <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">{event.symbol}</span>
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
  );
}
