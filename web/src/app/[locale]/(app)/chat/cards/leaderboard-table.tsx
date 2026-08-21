"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  asArray,
  asNumber,
  asString,
  formatNumber,
  PLUMBING_KEYS,
} from "@/app/[locale]/(app)/chat/cards/card-format";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { cn } from "@/lib/cn";

const MAX_ROWS = 25;

/** Fields the backend stores as a 0–1 fraction (confirmed against each field's source: yfinance's
 * raw `pctHeld`/`pctChange` for `InstitutionalHolderRow`, `holders.py`; sector/industry weight for
 * `SectorOverviewCompanyRow`/`IndustryOverviewCompanyRow`, `discovery.py`) — rendered here as a
 * percentage (`*100` + `%` suffix), never the bare fraction `formatNumber` would otherwise print
 * ("0.08" instead of "8.00%"). Scoped to these specific keys, not every field whose name contains
 * "pct"/"weight" — several sibling fields (`active_etf_report`'s `current_weight`, `fund_holdings`'
 * own `holdings` weight) are already 0-100-scaled at the source and must NOT be multiplied again. */
const FRACTION_FIELDS = new Set(["pct_held", "pct_change", "market_weight"]);

/** Many distinct symbols/entities ranked or filtered (`score_leaderboard`, `equity_screener`,
 * `sector_overview`, `chip_flows`, ...) — the row list itself may live under `rows`, `hits`,
 * `results`, or a ranking-specific key (`inflow_ranking`), so the first array-valued field is
 * used generically rather than naming every backend key here. */
export function LeaderboardTable({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const rows = extractRows(data);
  if (rows.length === 0) {
    return null;
  }
  const displayRows = rows.slice(0, MAX_ROWS);
  const columns = columnsFor(displayRows);
  const isNumericColumn = numericColumnsFor(displayRows, columns);

  return (
    <div className="mt-3 overflow-x-auto rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            {columns.map((col) => (
              <th
                key={col}
                className={cn(
                  "border-[var(--border)] border-b px-3 py-2 font-medium text-[var(--text-muted)]",
                  isNumericColumn.has(col) ? "text-right" : "text-left",
                )}
              >
                {fieldLabel(t, col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((row, i) => (
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
                      ? renderNumericCell(value, col, locale)
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
          {t("moreRows", { count: rows.length - MAX_ROWS })}
        </p>
      ) : null}
    </div>
  );
}

/** Prefers the row's own `symbol`/`broker_id`/`holder_name` identity field, suffixed with the row's
 * index — some payloads repeat that identity field across rows (e.g. one symbol appearing once per
 * date in a per-period breakdown), so the identity field alone is not always unique; the index
 * suffix guarantees uniqueness while the identity field still gives React a stable-ish key across
 * re-renders where row order doesn't change. */
function rowKey(row: Record<string, unknown>, index: number): string {
  const identity =
    asString(row.symbol) ?? asString(row.broker_id) ?? asString(row.holder_name) ?? "row";
  return `${identity}-${index}`;
}

function renderNumericCell(value: number, column: string, locale: string): string {
  if (FRACTION_FIELDS.has(column)) {
    return `${formatNumber(value * 100, locale, { decimals: 2 })}%`;
  }
  return formatNumber(asNumber(value), locale);
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

/** A column is right-aligned (numeric) only if EVERY row's value for it is a `number` — matches
 * the per-cell check below exactly, so a header never claims an alignment its own column's cells
 * don't actually use (the bug this fixes: headers were always `text-left` regardless of the
 * numeric-right-aligned cells beneath them, visibly misaligned in a leaderboard/table card with
 * any numeric column). */
function numericColumnsFor(rows: Record<string, unknown>[], columns: string[]): Set<string> {
  const numeric = new Set<string>();
  for (const col of columns) {
    if (rows.every((row) => typeof row[col] === "number")) {
      numeric.add(col);
    }
  }
  return numeric;
}

const ROW_LIST_KEYS = [
  "rows",
  "hits",
  "results",
  "holders",
  "inflow_ranking",
  "outflow_ranking",
  "contracts",
];

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
