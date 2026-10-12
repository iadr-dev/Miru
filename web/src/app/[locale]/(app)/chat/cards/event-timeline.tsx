"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { fieldLabel } from "@/lib/field-labels";
import { asArray, asString, formatNumber, PLUMBING_KEYS, rowDate } from "@/lib/format-number";

const TITLE_KEYS = ["insider_name", "broker_name", "trade_type", "fiscal_year"];
/** `symbol`/`market` identify WHICH row this is, already established by the query context (a
 * single-symbol card never needs its own symbol repeated per row) — surfacing them here would
 * spend the 4-field summary budget on redundant identity instead of the row's actual content
 * (e.g. `cash_dividend`/`cash_ex_dividend_date`), which is the bug this skip-list fixes. */
const IDENTITY_KEYS = new Set(["symbol", "market"]);
const MAX_EVENTS = 15;

/** Discrete dated occurrences (`insider_activity`, `block_trade`, `dividend_schedule`, ...) — a
 * vertical list with the date pinned left and the event's other scalar fields as a compact
 * inline summary, newest first. */
export function EventTimeline({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const rows = extractRows(data);
  if (rows.length === 0) {
    return null;
  }
  const sorted = [...rows].sort((a, b) => (rowDate(b) ?? "").localeCompare(rowDate(a) ?? ""));

  return (
    <ul className="mt-3 flex flex-col divide-y divide-[var(--border)] surface-card">
      {sorted.slice(0, MAX_EVENTS).map((row, index) => {
        const date = rowDate(row) ?? "—";
        const summary = eventSummary(row, locale, t);
        // `${date}|${summary}` alone collides whenever two rows share both a missing date and an
        // identical summary (e.g. two insider-activity rows for the same officer title with no
        // `trade_date` and no other distinguishing scalar field) — the index breaks that tie.
        // Safe here: `sorted` is a fixed snapshot from one render's `data` prop, never reordered
        // or mutated in place after this map runs.
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: tie-breaker only, see comment above.
          <li key={`${date}|${summary}|${index}`} className="flex items-start gap-3 px-4 py-3">
            <span className="shrink-0 font-num text-[12px] text-[var(--text-muted)] tabular-nums">
              {date}
            </span>
            <span className="min-w-0 flex-1 text-[13px] text-[var(--text)]">{summary}</span>
          </li>
        );
      })}
    </ul>
  );
}

function eventSummary(
  row: Record<string, unknown>,
  locale: string,
  t: ReturnType<typeof useTranslations>,
): string {
  const title = TITLE_KEYS.map((key) => asString(row[key])).find((v) => v !== null);
  const parts: string[] = [];
  if (title) {
    parts.push(title);
  }
  for (const [key, value] of Object.entries(row)) {
    if (
      key === "trade_date" ||
      key === "date" ||
      TITLE_KEYS.includes(key) ||
      IDENTITY_KEYS.has(key) ||
      PLUMBING_KEYS.has(key)
    ) {
      continue;
    }
    const label = fieldLabel(t, key);
    if (typeof value === "number") {
      parts.push(`${label}: ${formatNumber(value, locale)}`);
    } else if (typeof value === "string" && value.length > 0) {
      parts.push(`${label}: ${value}`);
    }
    if (parts.length >= 4) {
      break;
    }
  }
  return parts.join(" · ") || "—";
}

const ROW_LIST_KEYS = ["rows", "events", "transactions"];

function extractRows(data: unknown): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  for (const key of ROW_LIST_KEYS) {
    if (Array.isArray(record[key])) {
      return asArray(record[key]);
    }
  }
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}
