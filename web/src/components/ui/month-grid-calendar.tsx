"use client";

import { CaretLeftIcon, CaretRightIcon } from "@phosphor-icons/react/dist/ssr";
import { type ReactNode, useMemo } from "react";
import { cn } from "@/lib/cn";
import { buildMonthGrid, toIsoDate } from "@/lib/month-grid";

/**
 * The month-nav header + 7-column day grid shared by every calendar-shaped card on the stock
 * detail page (`AnnouncementsCalendarView` in basic-info-panel.tsx, `StockEventsCalendarView` in
 * research-charts-panel.tsx) — pulled out once both callers turned out to be a byte-identical
 * copy of the same grid/dot/selection markup (flagged by SonarCloud's duplication check).
 * Callers own their own data shape/filtering/list rendering; this component only owns the
 * calendar surface: month nav, day cells, per-day dot indicators, and the selected-day toggle.
 *
 * Deliberately takes `prevMonthLabel`/`nextMonthLabel`/`monthTitle` as plain strings rather than
 * reading its own `useTranslations` namespace — the two callers live in different i18n
 * namespaces (`stock.basicInfo` vs `stock.research`) that happen to carry the same three keys;
 * passing rendered strings keeps this component namespace-agnostic instead of picking one
 * caller's namespace as if it were canonical.
 */
export function MonthGridCalendar({
  year,
  month,
  dotsByDay,
  selectedDay,
  onSelectDay,
  onPrevMonth,
  onNextMonth,
  prevMonthLabel,
  nextMonthLabel,
  monthTitle,
}: {
  readonly year: number;
  readonly month: number;
  readonly dotsByDay: ReadonlyMap<string, readonly string[]>;
  readonly selectedDay: string | null;
  readonly onSelectDay: (day: string | null) => void;
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
  readonly prevMonthLabel: string;
  readonly nextMonthLabel: string;
  readonly monthTitle: string;
}): ReactNode {
  const grid = useMemo(() => buildMonthGrid(year, month), [year, month]);
  const todayIso = useMemo(() => toIsoDate(new Date()), []);

  return (
    <div>
      <div className="mb-3 flex items-center justify-center gap-4">
        <button
          type="button"
          onClick={onPrevMonth}
          aria-label={prevMonthLabel}
          className="flex size-8 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          <CaretLeftIcon size={16} weight="regular" />
        </button>
        <span className="font-semibold text-[15px] text-[var(--text)]">{monthTitle}</span>
        <button
          type="button"
          onClick={onNextMonth}
          aria-label={nextMonthLabel}
          className="flex size-8 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          <CaretRightIcon size={16} weight="regular" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {grid.map(({ date, inMonth }) => {
          const iso = toIsoDate(date);
          const dots = dotsByDay.get(iso) ?? [];
          const isSelected = iso === selectedDay;
          return (
            <button
              key={iso}
              type="button"
              disabled={!inMonth}
              onClick={() => onSelectDay(selectedDay === iso ? null : iso)}
              aria-pressed={isSelected}
              className={cn(
                "flex min-h-12 flex-col items-center gap-1 rounded-[var(--radius-input)] py-1.5 text-[12px] transition-colors",
                !inMonth && "text-[var(--text-muted)] opacity-40",
                inMonth && !isSelected && "text-[var(--text)] hover:bg-[var(--surface-2)]",
                isSelected && "bg-[var(--accent)] font-semibold text-[color:#06231F]",
                iso === todayIso && !isSelected && "ring-1 ring-[var(--accent)]",
              )}
            >
              <span className="font-num tabular-nums">{date.getDate()}</span>
              {dots.length > 0 ? (
                <span className="flex gap-0.5" aria-hidden>
                  {dots.slice(0, 3).map((color) => (
                    <span
                      key={color}
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                  ))}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
