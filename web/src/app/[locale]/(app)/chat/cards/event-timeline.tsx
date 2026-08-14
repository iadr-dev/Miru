"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import {
  asArray,
  asString,
  formatNumber,
  PLUMBING_KEYS,
  rowDate,
} from "@/app/[locale]/(app)/chat/cards/card-format";

const TITLE_KEYS = ["insider_name", "broker_name", "trade_type", "fiscal_year"];
const MAX_EVENTS = 15;

/** Discrete dated occurrences (`insider_activity`, `block_trade`, `dividend_schedule`, ...) — a
 * vertical list with the date pinned left and the event's other scalar fields as a compact
 * inline summary, newest first. */
export function EventTimeline({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const rows = extractRows(data);
  if (rows.length === 0) {
    return null;
  }
  const sorted = [...rows].sort((a, b) => (rowDate(b) ?? "").localeCompare(rowDate(a) ?? ""));

  return (
    <ul className="mt-3 flex flex-col divide-y divide-[var(--border)] rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]">
      {sorted.slice(0, MAX_EVENTS).map((row) => {
        const date = rowDate(row) ?? "—";
        const summary = eventSummary(row, locale);
        return (
          <li key={`${date}|${summary}`} className="flex items-start gap-3 px-4 py-3">
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

function eventSummary(row: Record<string, unknown>, locale: string): string {
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
      PLUMBING_KEYS.has(key)
    ) {
      continue;
    }
    if (typeof value === "number") {
      parts.push(`${key}: ${formatNumber(value, locale)}`);
    } else if (typeof value === "string" && value.length > 0) {
      parts.push(`${key}: ${value}`);
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
