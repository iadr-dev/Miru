"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import {
  asArray,
  asNumber,
  asString,
  formatNumber,
  PLUMBING_KEYS,
} from "@/app/[locale]/(app)/chat/cards/card-format";

const MAX_ROWS = 25;

/** Many distinct symbols/entities ranked or filtered (`score_leaderboard`, `equity_screener`,
 * `sector_overview`, `chip_flows`, ...) — the row list itself may live under `rows`, `hits`,
 * `results`, or a ranking-specific key (`inflow_ranking`), so the first array-valued field is
 * used generically rather than naming every backend key here. */
export function LeaderboardTable({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const rows = extractRows(data);
  if (rows.length === 0) {
    return null;
  }
  const columns = columnsFor(rows.slice(0, MAX_ROWS));

  return (
    <div className="mt-3 overflow-x-auto rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            {columns.map((col) => (
              <th
                key={col}
                className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, MAX_ROWS).map((row, i) => (
            <tr key={rowKey(row, i)} className="hover:bg-[var(--surface-2)]">
              {columns.map((col) => {
                const value = row[col];
                return (
                  <td
                    key={col}
                    className={
                      typeof value === "number"
                        ? "border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums"
                        : "border-[var(--border)] border-b px-3 py-2 text-[var(--text)]"
                    }
                  >
                    {typeof value === "number"
                      ? formatNumber(asNumber(value), locale)
                      : renderCell(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_ROWS ? (
        <p className="border-[var(--border)] border-t px-3 py-2 text-[12px] text-[var(--text-muted)]">
          +{rows.length - MAX_ROWS} more
        </p>
      ) : null}
    </div>
  );
}

/** Prefers the row's own `symbol`/`broker_id`/`holder_name` identity field as the React key; a
 * row with none of those falls back to its index — no worse than the array-index-key default,
 * only reached when the payload genuinely has no natural per-row identity. */
function rowKey(row: Record<string, unknown>, index: number): string {
  return (
    asString(row.symbol) ?? asString(row.broker_id) ?? asString(row.holder_name) ?? String(index)
  );
}

function renderCell(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((v) => asString(v) ?? String(v)).join(", ");
  }
  return asString(value) ?? (value === null || value === undefined ? "—" : String(value));
}

function columnsFor(rows: Record<string, unknown>[]): string[] {
  const columns = new Set<string>();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (
        !PLUMBING_KEYS.has(key) &&
        (typeof value === "string" ||
          typeof value === "number" ||
          typeof value === "boolean" ||
          Array.isArray(value))
      ) {
        columns.add(key);
      }
    }
  }
  return [...columns];
}

const ROW_LIST_KEYS = ["rows", "hits", "results", "holders", "inflow_ranking", "outflow_ranking"];

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
  // A nested `result: { hits: [...] }` envelope (equity_screener, tag_screener).
  const result = record.result;
  if (result && typeof result === "object") {
    return extractRows(result);
  }
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}
