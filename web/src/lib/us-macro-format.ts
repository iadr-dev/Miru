/**
 * Formatting for the US macro section. Every number goes through `Intl.NumberFormat` (locale-aware);
 * a change is plain signed text, never coloured or glyphed as a price move (web/OVERVIEW.md §4).
 */

import { formatNumber } from "@/lib/format-number";
import type { UnitKind } from "@/types/market/us-macro";

/** Decimal places by what is measured. A `level` (e.g. millions of dollars) is shown whole once
 * it is large, so thousands separators do the work instead of fractions. */
function decimalsFor(kind: UnitKind, value: number): number {
  switch (kind) {
    case "percent":
      return 2;
    case "index":
      return 1;
    case "count":
      return 0;
    case "level":
      return Math.abs(value) >= 1000 ? 0 : 2;
    default:
      return assertNever(kind);
  }
}

function assertNever(value: never): never {
  throw new Error(`Unhandled unit kind: ${String(value)}`);
}

export function formatMacroValue(value: number | null, kind: UnitKind, locale: string): string {
  if (value === null) {
    return "—";
  }
  const text = formatNumber(value, locale, { decimals: decimalsFor(kind, value) });
  return kind === "percent" ? `${text}%` : text;
}

/** A change vs the prior observation as signed text: "+0.20", "-1.5", "0.00". For a percent series
 * the unit is percentage points, left implicit on narrow rows. */
export function formatMacroChange(change: number | null, kind: UnitKind, locale: string): string {
  if (change === null) {
    return "—";
  }
  const decimals = decimalsFor(kind, change);
  const text = formatNumber(Math.abs(change), locale, { decimals });
  const isZero = Number(Math.abs(change).toFixed(decimals)) === 0;
  if (isZero) {
    return text;
  }
  return `${change > 0 ? "+" : "-"}${text}`;
}

export function formatMacroDay(iso: string, locale: string): string {
  const date = parseIsoDate(iso);
  if (date === null) {
    return iso;
  }
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/** Heading form for a release day: "10月16日 週五". */
export function formatMacroWeekdayDate(iso: string, locale: string): string {
  const date = parseIsoDate(iso);
  if (date === null) {
    return iso;
  }
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    weekday: "short",
    timeZone: "UTC",
  }).format(date);
}

export function formatMacroMonth(iso: string, locale: string): string {
  const date = parseIsoDate(iso);
  if (date === null) {
    return iso;
  }
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(date);
}

export function parseIsoDate(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) {
    return null;
  }
  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Days a series can go without a new observation before it reads as "older than usual", by its
 * publication frequency (a daily series trails by a business day, a monthly one by weeks). */
const STALE_AFTER_DAYS: Record<string, number> = {
  daily: 7,
  weekly: 21,
  monthly: 75,
  quarterly: 150,
  annual: 450,
};

const MS_PER_DAY = 86_400_000;

/** True when the latest observation is older than its frequency normally allows, relative to
 * `asOf` (the data's own as-of date, so the check does not drift with the viewer's clock). */
export function isOlderThanUsual(
  lastObservationDate: string | null,
  frequency: string,
  asOf: string | null,
): boolean {
  const last = lastObservationDate ? parseIsoDate(lastObservationDate) : null;
  const reference = asOf ? parseIsoDate(asOf) : null;
  const limit = STALE_AFTER_DAYS[frequency.toLowerCase()];
  if (last === null || reference === null || limit === undefined) {
    return false;
  }
  return (reference.getTime() - last.getTime()) / MS_PER_DAY > limit;
}

/** Monday to Sunday (inclusive ISO dates) of the week containing `now`, by the viewer's local
 * calendar date — the bounds of the releases feed's 本週 filter. */
export function currentWeekBounds(now: Date): { start: string; end: string } {
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const sinceMonday = (new Date(today).getUTCDay() + 6) % 7;
  const start = today - sinceMonday * MS_PER_DAY;
  const end = start + 6 * MS_PER_DAY;
  return {
    start: new Date(start).toISOString().slice(0, 10),
    end: new Date(end).toISOString().slice(0, 10),
  };
}
