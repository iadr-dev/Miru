"use client";

import { CaretLeftIcon, CaretRightIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { DividendCalendarEventRow } from "@/types/market";

const WEEKDAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"] as const;
// Same collapsed-row-count convention `DAILY_SIGNAL_COLLAPSED_ROW_COUNT`/`HolderAccumulationCard`
// use — enough events visible inline while keeping this card's height comparable to its
// dashboard neighbors; "展開完整{count}筆" opens the full (still day-filtered, if a day is
// selected) list in `ExpandModal`.
const COLLAPSED_EVENT_COUNT = 10;

// 3 distinct categorical dot colors — one per date type (除息日/最後購買日/股利發放日), never
// --up/--down (this is a date-type classification, not a price/%/score signal, per
// web/CLAUDE.md §4 — same reasoning `daily-signal-table.tsx`'s own STATUS_TOKEN documents).
const DOT_COLOR = {
  ex: "var(--chart-1)",
  lastBuy: "var(--chart-4)",
  payment: "var(--chart-3)",
} as const;
type DotKind = keyof typeof DOT_COLOR;

// Inspired by Outlook's month-grid chip-in-cell + "+N more" overflow (the second reference
// screenshot) — but capped hard at 2, since a single ex-date can carry 30+ ETFs (e.g. 2026-08-18)
// and the cell must stay a fixed, predictable height rather than growing per-day.
const MAX_CHIPS_PER_DAY = 2;

/** One ETF's marks for a single day — which of the 3 date types land on it (an ex-date and a
 * last-buy-date from two different events can coincide on the same symbol or across symbols),
 * driving how many dots render next to that one chip. */
interface SymbolMarks {
  symbol: string;
  ex: boolean;
  lastBuy: boolean;
  payment: boolean;
}

/** One calendar day's cell content — the distinct ETF symbols landing on it (one chip per
 * symbol, never per date-type, so an ex-date + last-buy-date coinciding on 0050 stays a single
 * chip with two dots instead of doubling the row count) plus a total count for the "+N more"
 * overflow line. */
interface DayMarks {
  symbols: SymbolMarks[];
  totalCount: number;
}

function buildDayMarks(events: readonly DividendCalendarEventRow[]): Map<string, DayMarks> {
  const bySymbol = new Map<string, Map<string, SymbolMarks>>();
  const mark = (isoDate: string, symbol: string, kind: DotKind): void => {
    const daySymbols = bySymbol.get(isoDate) ?? new Map<string, SymbolMarks>();
    const existing = daySymbols.get(symbol) ?? {
      symbol,
      ex: false,
      lastBuy: false,
      payment: false,
    };
    existing[kind] = true;
    daySymbols.set(symbol, existing);
    bySymbol.set(isoDate, daySymbols);
  };
  for (const event of events) {
    mark(event.ex_dividend_date, event.symbol, "ex");
    mark(event.last_buy_date, event.symbol, "lastBuy");
    if (event.payment_date) mark(event.payment_date, event.symbol, "payment");
  }
  const marks = new Map<string, DayMarks>();
  for (const [isoDate, daySymbols] of bySymbol) {
    const symbols = [...daySymbols.values()];
    marks.set(isoDate, { symbols, totalCount: symbols.length });
  }
  return marks;
}

/** Every calendar cell for one month view, including the leading/trailing days from the
 * adjacent months that fill the grid — `inMonth: false` cells render dimmed and are never
 * selectable, matching the reference app's own faded-adjacent-month convention. */
function buildMonthGrid(year: number, month: number): { date: Date; inMonth: boolean }[] {
  const firstOfMonth = new Date(year, month - 1, 1);
  const firstWeekday = firstOfMonth.getDay();
  const daysInMonth = new Date(year, month, 0).getDate();
  const daysInPrevMonth = new Date(year, month - 1, 0).getDate();

  const cells: { date: Date; inMonth: boolean }[] = [];
  for (let i = firstWeekday - 1; i >= 0; i--) {
    cells.push({ date: new Date(year, month - 2, daysInPrevMonth - i), inMonth: false });
  }
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push({ date: new Date(year, month - 1, day), inMonth: true });
  }
  while (cells.length % 7 !== 0) {
    const last = cells[cells.length - 1];
    const nextDay = (last ? last.date.getDate() : 0) + 1;
    cells.push({ date: new Date(year, month, nextDay), inMonth: false });
  }
  return cells;
}

function toIsoDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/**
 * 股息日曆 — a month-grid calendar of every ETF's 除息日 (ex-dividend)/最後購買日 (last buy
 * day)/股利發放日 (payment date). On wide viewports each day cell shows up to
 * `MAX_CHIPS_PER_DAY` symbol chips (dot(s) + ticker) plus a "+N more" overflow line — inspired by
 * Outlook's month-grid event-chip-per-cell convention (a second reference screenshot), scaled down
 * from Outlook's ~180px desktop columns to this card's dashboard-width columns via the hard chip
 * cap. Below `sm` this collapses to the original dot-only summary (the first, mobile-app reference
 * screenshot's own convention), since a real ticker string doesn't fit legibly in a narrow cell.
 * Clicking a day filters the events list below to that day only; clicking again (or clicking
 * outside any day) clears the filter back to showing the whole month. Month navigation is owned by
 * the caller (`DividendCalendarPanel`) — this component is pure presentation over whatever
 * `events`/`year`/`month` it's given, the same lift-state-up split `HolderAccumulationCard`'s
 * threshold/window toggles use.
 */
export function DividendCalendarTable({
  year,
  month,
  events,
  onPrevMonth,
  onNextMonth,
}: {
  year: number;
  month: number;
  events: readonly DividendCalendarEventRow[];
  onPrevMonth: () => void;
  onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("market.cards.dividendCalendar");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const grid = useMemo(() => buildMonthGrid(year, month), [year, month]);
  const dayMarks = useMemo(() => buildDayMarks(events), [events]);
  const todayIso = useMemo(() => toIsoDate(new Date()), []);

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
      <div className="mb-3 flex items-center justify-center gap-4">
        <button
          type="button"
          onClick={onPrevMonth}
          aria-label={t("prevMonth")}
          className="flex size-8 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          <CaretLeftIcon size={16} weight="regular" />
        </button>
        <span className="font-semibold text-[15px] text-[var(--text)]">
          {t("monthTitle", { year, month })}
        </span>
        <button
          type="button"
          onClick={onNextMonth}
          aria-label={t("nextMonth")}
          className="flex size-8 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          <CaretRightIcon size={16} weight="regular" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-[var(--text-muted)]">
        {WEEKDAY_KEYS.map((key) => (
          <span key={key} className="py-1">
            {t(`weekday.${key}`)}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {grid.map(({ date, inMonth }) => {
          const iso = toIsoDate(date);
          const marks = dayMarks.get(iso);
          const isToday = iso === todayIso;
          const isSelected = iso === selectedDay;
          const shownSymbols = marks?.symbols.slice(0, MAX_CHIPS_PER_DAY) ?? [];
          const overflowCount = marks ? marks.totalCount - shownSymbols.length : 0;
          return (
            <button
              key={iso}
              type="button"
              disabled={!inMonth}
              onClick={() => setSelectedDay((prev) => (prev === iso ? null : iso))}
              aria-pressed={isSelected}
              aria-label={
                marks
                  ? t("dayAriaWithEvents", { day: date.getDate() })
                  : t("dayAria", { day: date.getDate() })
              }
              className={cn(
                "flex min-h-14 flex-col items-start gap-0.5 rounded-[var(--radius-input)] px-1 py-1.5 text-[13px] transition-colors sm:min-h-24",
                !inMonth && "text-[var(--text-muted)] opacity-40",
                inMonth && !isSelected && "text-[var(--text)] hover:bg-[var(--surface-2)]",
                isSelected && "bg-[var(--accent)] font-semibold text-[color:#06231F]",
                isToday && !isSelected && "ring-1 ring-[var(--accent)]",
              )}
            >
              <span className="w-full text-center font-num tabular-nums">{date.getDate()}</span>

              {/* Chip-in-cell + "+N more" overflow — desktop/wide-card only (Outlook-inspired,
                  see the component docstring); collapses to the dot-only summary below `sm` since
                  a real ETF symbol string doesn't fit legibly in a narrow cell. */}
              {shownSymbols.length > 0 ? (
                <span className="hidden w-full flex-col gap-0.5 sm:flex" aria-hidden>
                  {shownSymbols.map((symbolMarks) => (
                    <span
                      key={symbolMarks.symbol}
                      className="flex items-center gap-0.5 truncate text-[10px] leading-tight"
                    >
                      {symbolMarks.ex ? (
                        <span
                          className="size-1 shrink-0 rounded-full"
                          style={{ backgroundColor: DOT_COLOR.ex }}
                        />
                      ) : null}
                      {symbolMarks.lastBuy ? (
                        <span
                          className="size-1 shrink-0 rounded-full"
                          style={{ backgroundColor: DOT_COLOR.lastBuy }}
                        />
                      ) : null}
                      {symbolMarks.payment ? (
                        <span
                          className="size-1 shrink-0 rounded-full"
                          style={{ backgroundColor: DOT_COLOR.payment }}
                        />
                      ) : null}
                      <span className="truncate">{symbolMarks.symbol}</span>
                    </span>
                  ))}
                  {overflowCount > 0 ? (
                    <span className="text-[10px] text-[var(--text-muted)] leading-tight">
                      {t("moreCount", { count: overflowCount })}
                    </span>
                  ) : null}
                </span>
              ) : null}

              {marks ? (
                <span className="flex gap-0.5 sm:hidden" aria-hidden>
                  {marks.symbols.some((s) => s.ex) ? (
                    <span
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: DOT_COLOR.ex }}
                    />
                  ) : null}
                  {marks.symbols.some((s) => s.lastBuy) ? (
                    <span
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: DOT_COLOR.lastBuy }}
                    />
                  ) : null}
                  {marks.symbols.some((s) => s.payment) ? (
                    <span
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: DOT_COLOR.payment }}
                    />
                  ) : null}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted)]">
        <LegendDot color={DOT_COLOR.ex} label={t("legendExDate")} />
        <LegendDot color={DOT_COLOR.lastBuy} label={t("legendLastBuyDate")} />
        <LegendDot color={DOT_COLOR.payment} label={t("legendPaymentDate")} />
      </div>

      <DividendCalendarEventList events={visibleEvents.slice(0, COLLAPSED_EVENT_COUNT)} />
      {visibleEvents.length > COLLAPSED_EVENT_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2 w-full rounded-[var(--radius-button)] py-2 text-[13px] text-[var(--accent)] transition-colors hover:bg-[var(--surface-2)]"
        >
          {t("expandTo", { count: visibleEvents.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <DividendCalendarEventList events={visibleEvents} />
      </ExpandModal>
    </div>
  );
}

/** Shared row-rendering path for both the inline (sliced) preview and the `ExpandModal`'s full
 * list — so the two never drift in columns/formatting, the same convention
 * `daily-signal-panel.tsx`'s own `filterDailySignalRows` render path uses. */
function DividendCalendarEventList({
  events,
}: {
  events: readonly DividendCalendarEventRow[];
}): ReactNode {
  const t = useTranslations("market.cards.dividendCalendar");
  const locale = useLocale();

  if (events.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <ul className="mt-3 divide-y divide-[var(--border)]">
      {events.map((event) => (
        <li key={`${event.symbol}-${event.ex_dividend_date}`} className="py-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[13px] text-[var(--text)]">
              {event.name}
              <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{event.symbol}</span>
            </span>
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

function LegendDot({ color, label }: { color: string; label: string }): ReactNode {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2 rounded-full" style={{ backgroundColor: color }} aria-hidden />
      {label}
    </span>
  );
}

function DateChip({
  color,
  label,
  date,
}: {
  color: string;
  label: string;
  date: string;
}): ReactNode {
  return (
    <span className="flex items-center gap-1">
      <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} aria-hidden />
      {label} {date}
    </span>
  );
}
