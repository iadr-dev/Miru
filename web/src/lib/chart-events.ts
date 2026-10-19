/** Corporate events for the TW candlestick chart — pure helpers (no React, no chart library).
 *
 * Sources are the calendar feed (`/stock/calendar-events`: ex-rights/dividend, AGM, cash capital
 * increase) and the settled/announced dividend policy rows (`/stock/dividend-policy-summary`:
 * cash / stock ex-dates with amounts). There is deliberately NO earnings-date kind: no
 * per-symbol Taiwan earnings-date source exists, so none is invented here. */

import type { DividendEvent } from "@/types/stock/dividends";
import type { CalendarEventOut } from "@/types/stock/events";

export type ChartEventKind =
  | "exDividend"
  | "exRights"
  | "exDividendRights"
  | "agm"
  | "capitalIncrease";

export interface ChartEvent {
  readonly date: string;
  readonly kind: ChartEventKind;
  /** Cash / stock dividend per share — only from the dividend rows. */
  readonly cash?: number | undefined;
  readonly stock?: number | undefined;
  /** The event date is after `today`. */
  readonly upcoming: boolean;
}

/** Categorical colour token + marker shape per kind — never `--up` / `--down`. */
export const CHART_EVENT_STYLE: Record<
  ChartEventKind,
  { readonly token: string; readonly shape: "square" | "circle" | "arrowDown" }
> = {
  exDividend: { token: "var(--chart-1)", shape: "square" },
  exRights: { token: "var(--chart-1)", shape: "square" },
  exDividendRights: { token: "var(--chart-1)", shape: "square" },
  agm: { token: "var(--chart-4)", shape: "circle" },
  capitalIncrease: { token: "var(--chart-3)", shape: "arrowDown" },
};

const CALENDAR_KIND: Record<string, ChartEventKind> = {
  dividend_ex_rights: "exDividendRights",
  agm: "agm",
  cash_capital_increase_rights: "capitalIncrease",
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

function isoDay(value: string | null | undefined): string | null {
  if (!value || !ISO_DATE.test(value)) return null;
  return value.slice(0, 10);
}

function positive(value: number | null | undefined): number | undefined {
  return value !== null && value !== undefined && value > 0 ? value : undefined;
}

/** Merges the calendar feed and the dividend rows into one list, de-duplicated by (date, kind).
 * The dividend rows carry the amounts and distinguish 除息 from 除權, so on a date they cover they
 * win over the calendar's combined 除權息 entry. Newest first. */
export function buildChartEvents(
  calendar: readonly CalendarEventOut[],
  dividends: readonly DividendEvent[],
  today: string,
): ChartEvent[] {
  const byKey = new Map<string, ChartEvent>();
  const add = (event: Omit<ChartEvent, "upcoming">): void => {
    const key = `${event.date}|${event.kind}`;
    const existing = byKey.get(key);
    byKey.set(key, {
      ...event,
      cash: existing?.cash ?? event.cash,
      stock: existing?.stock ?? event.stock,
      upcoming: event.date > today,
    });
  };

  const dividendDates = new Set<string>();
  for (const row of dividends) {
    const cashDate = isoDay(row.cash_ex_dividend_date);
    if (cashDate) {
      dividendDates.add(cashDate);
      add({ date: cashDate, kind: "exDividend", cash: positive(row.cash_dividend) });
    }
    const stockDate = isoDay(row.stock_ex_dividend_date);
    if (stockDate) {
      dividendDates.add(stockDate);
      add({ date: stockDate, kind: "exRights", stock: positive(row.stock_dividend) });
    }
  }
  for (const row of calendar) {
    const kind = CALENDAR_KIND[row.type];
    const date = isoDay(row.event_date) ?? isoDay(row.window_start);
    if (!kind || !date) continue;
    if (kind === "exDividendRights" && dividendDates.has(date)) continue;
    add({ date, kind });
  }
  return [...byKey.values()].sort(
    (a, b) => b.date.localeCompare(a.date) || a.kind.localeCompare(b.kind),
  );
}

export interface SnappedEvents {
  /** The bar time (a date string that exists in `barDates`) the events sit on. */
  readonly time: string;
  readonly kinds: readonly ChartEventKind[];
}

/** Snaps each event to the first bar whose time is on or after the event date — for a 週/月
 * series (each bar is stamped with its bucket's last date) that is the bar containing the date.
 * Events before `rangeStart` (default: the first bar) or after the last bar are dropped, so a
 * marker never lands on a bar that does not exist. Several events on one bar are grouped. */
export function snapEventsToBars(
  events: readonly ChartEvent[],
  barDates: readonly string[],
  rangeStart?: string,
): SnappedEvents[] {
  const first = barDates[0];
  const last = barDates.at(-1);
  if (first === undefined || last === undefined) return [];
  const lower = rangeStart !== undefined && rangeStart < first ? rangeStart : first;
  const grouped = new Map<string, ChartEventKind[]>();
  for (const event of events) {
    if (event.date < lower || event.date > last) continue;
    const time = barDates.find((d) => d >= event.date);
    if (time === undefined) continue;
    const kinds = grouped.get(time) ?? [];
    if (!kinds.includes(event.kind)) kinds.push(event.kind);
    grouped.set(time, kinds);
  }
  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([time, kinds]) => ({ time, kinds }));
}

/** `createSeriesMarkers` can be called once per series (a second call replaces the first), so
 * every marker source is merged into one time-ordered array before it is handed over. */
export function mergeMarkers<M extends { readonly time: unknown }>(
  ...groups: readonly (readonly M[])[]
): M[] {
  return groups.flat().sort((a, b) => String(a.time).localeCompare(String(b.time)));
}
