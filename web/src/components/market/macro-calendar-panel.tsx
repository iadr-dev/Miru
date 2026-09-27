"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getMacroCalendar } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { safeHttpUrl } from "@/lib/safe-url";
import type { MacroReleaseEventRow } from "@/types/market";

const COLLAPSED_ROW_COUNT = 10;
const DAYS_AHEAD = 30;

const IMPACT_COLOR: Record<string, string> = {
  high: "var(--down)",
  medium: "var(--warn)",
  low: "var(--text-muted)",
};

const IMPACT_LABEL_KEY: Record<string, string> = {
  high: "impactHigh",
  medium: "impactMedium",
  low: "impactLow",
};

/** 總經 sub-tab's release-calendar card — real upcoming US/global macro-release events
 * (financecalendar.com, `get_macro_calendar`), a distinct third-party feed from the existing
 * `get_calendar_events_feed`'s yfinance economic calendar (additive, not a replacement — see
 * that tool's own docstring). Mirrors `earnings-calendar-panel.tsx`'s exact month-grouped list +
 * expand pattern; no per-symbol link here (a macro release has no ticker), and an impact-level
 * color chip stands in for `EarningsCalendarPanel`'s timing badge. */
export function MacroCalendarPanel(): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: ["macro_calendar", DAYS_AHEAD],
    queryFn: () => getMacroCalendar(undefined, DAYS_AHEAD),
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={380} />}>
        {(data) => {
          if (data.events.length === 0) {
            return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
          }
          const visible = expanded ? data.events : data.events.slice(0, COLLAPSED_ROW_COUNT);
          return (
            <>
              <MacroEventList events={visible} />
              {!expanded && data.events.length > COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: data.events.length })}</span>
                </button>
              ) : null}
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

/** Groups events by calendar month — same convention `earnings-calendar-panel.tsx`'s own
 * `groupEventsByMonth` establishes, duplicated narrowly here since the two row shapes (`date`
 * field name/format, no `symbol`) diverge enough that sharing one generic helper would need its
 * own key-extraction indirection for little gain over two small, obviously-parallel functions. */
function groupEventsByMonth(
  events: readonly MacroReleaseEventRow[],
): ReadonlyMap<string, MacroReleaseEventRow[]> {
  const groups = new Map<string, MacroReleaseEventRow[]>();
  for (const event of events) {
    const monthKey = event.date.slice(0, 7);
    const group = groups.get(monthKey);
    if (group) {
      group.push(event);
    } else {
      groups.set(monthKey, [event]);
    }
  }
  return groups;
}

function MacroEventList({
  events,
}: {
  readonly events: readonly MacroReleaseEventRow[];
}): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
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
                <MacroEventRow key={`${event.name}_${event.date}`} event={event} />
              ))}
            </ol>
          </div>
        );
      })}
    </div>
  );
}

function MacroEventRow({ event }: { readonly event: MacroReleaseEventRow }): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
  const color = IMPACT_COLOR[event.impact] ?? "var(--text-muted)";
  const href = event.url ? safeHttpUrl(event.url) : null;
  const impactLabelKey = IMPACT_LABEL_KEY[event.impact];
  const impactLabel = impactLabelKey ? t(impactLabelKey) : event.impact;

  return (
    <li className="flex items-center gap-3 py-2.5">
      <span
        className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
        aria-hidden
      />
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)] hover:underline"
        >
          {event.title}
        </a>
      ) : (
        <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
          {event.title}
        </span>
      )}
      <span
        className="shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]"
        style={{
          backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)`,
          color,
        }}
      >
        {impactLabel}
      </span>
      {event.consensus || event.prior || event.actual ? (
        <span className="hidden shrink-0 items-center gap-2 text-[11px] text-[var(--text-muted)] md:flex">
          {event.prior ? (
            <span>
              {t("prior")}: {event.prior}
            </span>
          ) : null}
          {event.consensus ? (
            <span>
              {t("consensus")}: {event.consensus}
            </span>
          ) : null}
          {event.actual ? (
            <span>
              {t("actual")}: {event.actual}
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="font-num shrink-0 text-[11px] text-[var(--text-muted)] tabular-nums">
        {event.date}
      </span>
    </li>
  );
}
