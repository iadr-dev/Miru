"use client";

import { StarIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ExpandModal } from "@/components/ui/expand-modal";
import { MonthGridCalendar } from "@/components/ui/month-grid-calendar";
import { ViewToggleButton } from "@/components/ui/view-toggle-button";
import { getMacroCalendar } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { safeHttpUrl } from "@/lib/safe-url";
import type { MacroReleaseEventRow } from "@/types/market";

const COLLAPSED_ROW_COUNT = 10;
const DAYS_AHEAD = 30;
const MACRO_ROW_GRID_COLS = "grid-cols-[64px_1.6fr_88px_100px_100px_100px]";

type CalendarView = "list" | "calendar";

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

// 重要性 star count per severity — a visual density cue alongside the color chip, not a
// fabricated signal (unlike a directional 利多/利空 tag, which financecalendar.com's own data
// has no field for — this codebase never invents one).
const IMPACT_STAR_COUNT: Record<string, number> = {
  high: 3,
  medium: 2,
  low: 1,
};
const MAX_IMPACT_STARS = 3;

/** 總經 sub-tab's release-calendar card — real upcoming US/global macro-release events
 * (financecalendar.com, `get_macro_calendar`), a distinct third-party feed from the existing
 * `get_calendar_events_feed`'s yfinance economic calendar (additive, not a replacement — see
 * that tool's own docstring). List view mirrors `earnings-calendar-panel.tsx`'s exact
 * month-grouped list + expand pattern; calendar view reuses the shared `MonthGridCalendar` +
 * `ViewToggleButton` pair `calendar-events-card.tsx`'s `UsMarketCalendarCard` already
 * established, so every calendar-shaped card in this app behaves the same. No per-symbol link
 * here (a macro release has no ticker), and an impact-level color chip stands in for
 * `EarningsCalendarPanel`'s timing badge. */
export function MacroCalendarPanel(): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<CalendarView>("list");
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() + 1 });

  const query = useQuery({
    queryKey: ["macro_calendar", DAYS_AHEAD],
    queryFn: () => getMacroCalendar(undefined, DAYS_AHEAD),
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        {query.data && query.data.events.length > 0 ? (
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
      <QueryState query={query} skeleton={<CardSkeletonBody height={380} />}>
        {(data) => {
          if (data.events.length === 0) {
            return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
          }
          if (view === "calendar") {
            return (
              <MacroEventCalendarView
                year={cursor.year}
                month={cursor.month}
                events={data.events}
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
    <div className="overflow-x-auto">
      <div className="flex min-w-[720px] flex-col gap-4">
        {[...groups.entries()].map(([monthKey, monthEvents]) => {
          const [yearStr, monthStr] = monthKey.split("-");
          return (
            <div key={monthKey}>
              <h3 className="mb-1 text-[12px] text-[var(--text-muted)]">
                {t("monthHeading", { year: Number(yearStr), month: Number(monthStr) })}
              </h3>
              <MacroEventHeaderRow />
              <ol className="divide-y divide-[var(--border)]">
                {monthEvents.map((event) => (
                  <MacroEventRow key={`${event.name}_${event.date}`} event={event} />
                ))}
              </ol>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MacroEventHeaderRow(): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
  return (
    <div
      className={cn(
        "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5",
        MACRO_ROW_GRID_COLS,
      )}
    >
      <span className="text-[11px] text-[var(--text-muted)]">{t("columnTime")}</span>
      <span className="text-[11px] text-[var(--text-muted)]">{t("columnEvent")}</span>
      <span className="text-[11px] text-[var(--text-muted)]">{t("columnImpact")}</span>
      <span className="text-right text-[11px] text-[var(--text-muted)]">{t("prior")}</span>
      <span className="text-right text-[11px] text-[var(--text-muted)]">{t("consensus")}</span>
      <span className="text-right text-[11px] text-[var(--text-muted)]">{t("actual")}</span>
    </div>
  );
}

/** Same left-list / right-grid two-column arrangement `EarningsCalendarView`
 * (`calendar-events-card.tsx`) uses over the shared `MonthGridCalendar` — impact color stands in
 * for that view's dot color, one dot per event on its release day (a day can carry more than one
 * release, so this maps to an array, not a single color). The side list is capped to
 * `COLLAPSED_ROW_COUNT` + a scrollable max-height so an unfiltered month (which can carry 50+
 * events across the whole `DAYS_AHEAD` window) never runs the list past the calendar grid's own
 * bottom edge — `ExpandModal` opens the full (or day-filtered) list instead. */
function MacroEventCalendarView({
  year,
  month,
  events,
  onPrevMonth,
  onNextMonth,
}: {
  readonly year: number;
  readonly month: number;
  readonly events: readonly MacroReleaseEventRow[];
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("market.cards.macroCalendar");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const dotsByDay = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const event of events) {
      const day = event.date;
      const colors = map.get(day) ?? [];
      colors.push(IMPACT_COLOR[event.impact] ?? "var(--text-muted)");
      map.set(day, colors);
    }
    return map;
  }, [events]);

  const visibleEvents = selectedDay ? events.filter((event) => event.date === selectedDay) : events;
  const previewEvents = visibleEvents.slice(0, COLLAPSED_ROW_COUNT);

  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-start">
      <div className="min-w-0 md:w-72 md:shrink-0">
        {visibleEvents.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
        ) : (
          <>
            <ol className="max-h-[420px] divide-y divide-[var(--border)] overflow-y-auto">
              {previewEvents.map((event) => (
                <MacroEventCompactRow key={`${event.name}_${event.date}`} event={event} />
              ))}
            </ol>
            {visibleEvents.length > COLLAPSED_ROW_COUNT ? (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className={cn(
                  "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                  "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                )}
              >
                <span>{t("expandTo", { count: visibleEvents.length })}</span>
              </button>
            ) : null}
            <ExpandModal
              open={expanded}
              title={t("title")}
              closeLabel={t("collapse")}
              onClose={() => setExpanded(false)}
            >
              <ol className="divide-y divide-[var(--border)]">
                {visibleEvents.map((event) => (
                  <MacroEventCompactRow key={`${event.name}_${event.date}`} event={event} />
                ))}
              </ol>
            </ExpandModal>
          </>
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

/** Full 6-column table row (時間/數據/重要性/前值/預測值/公布值) for the list view — header
 * cells in `MacroEventHeaderRow` use the identical `MACRO_ROW_GRID_COLS` template so labels and
 * values land in the same columns. `重要性` renders as impact-colored stars (count, not a
 * fabricated 利多/利空 direction — see `IMPACT_STAR_COUNT`'s own comment). */
function MacroEventRow({ event }: { readonly event: MacroReleaseEventRow }): ReactNode {
  const color = IMPACT_COLOR[event.impact] ?? "var(--text-muted)";
  const href = event.url ? safeHttpUrl(event.url) : null;
  const starCount = IMPACT_STAR_COUNT[event.impact] ?? 0;
  const timeLabel = event.time_utc
    ? new Date(event.time_utc).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      })
    : "—";

  return (
    <li className={cn("grid items-center gap-2.5 py-2.5", MACRO_ROW_GRID_COLS)}>
      <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        {timeLabel}
      </span>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="min-w-0 truncate text-[13px] text-[var(--text)] hover:underline"
        >
          {event.title}
        </a>
      ) : (
        <span className="min-w-0 truncate text-[13px] text-[var(--text)]">{event.title}</span>
      )}
      <span className="flex items-center gap-0.5" aria-hidden>
        {Array.from({ length: MAX_IMPACT_STARS }, (_, index) => (
          <StarIcon
            // `MAX_IMPACT_STARS` is a fixed constant, never reordered; each star has no other id.
            // biome-ignore lint/suspicious/noArrayIndexKey: tie-breaker only, see comment above.
            key={index}
            size={11}
            weight={index < starCount ? "fill" : "regular"}
            color={index < starCount ? color : "var(--border)"}
          />
        ))}
      </span>
      <span className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        {event.prior ?? "—"}
      </span>
      <span className="text-right font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        {event.consensus ?? "—"}
      </span>
      <span className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {event.actual ?? "—"}
      </span>
    </li>
  );
}

/** Single-line compact row for the calendar view's narrow (~288px) side list — the full
 * 6-column table (`MacroEventRow`) doesn't fit that width, so this keeps the original
 * dot+title+impact-chip+date layout for that one context. */
function MacroEventCompactRow({ event }: { readonly event: MacroReleaseEventRow }): ReactNode {
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
      <span className="font-num shrink-0 text-[11px] text-[var(--text-muted)] tabular-nums">
        {event.date}
      </span>
    </li>
  );
}
