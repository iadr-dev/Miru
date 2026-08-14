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

/** Line-item rows with a category dimension, often pivoted by period (`financial_statement`'s
 * `item` × `period_end` × `value`) or a flat roster (`insider_shareholding`, `theme_members`).
 * Pivots into a wide table when a period-like field repeats across rows for the same category;
 * otherwise renders one row per record. */
export function DataSheet({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const rows = extractRows(data);
  if (rows.length === 0) {
    return null;
  }

  const categoryKey = firstPresentKey(rows, ["item", "sub_industry", "person_name", "holder_name"]);
  const periodKey = firstPresentKey(rows, ["period_end", "trade_date"]);
  const valueKey = firstPresentKey(rows, ["value", "shares", "current_shares"]);

  if (categoryKey && periodKey && valueKey) {
    return (
      <PivotedSheet
        rows={rows}
        categoryKey={categoryKey}
        periodKey={periodKey}
        valueKey={valueKey}
        locale={locale}
      />
    );
  }
  return <FlatSheet rows={rows} locale={locale} />;
}

function PivotedSheet({
  rows,
  categoryKey,
  periodKey,
  valueKey,
  locale,
}: {
  rows: Record<string, unknown>[];
  categoryKey: string;
  periodKey: string;
  valueKey: string;
  locale: string;
}): ReactNode {
  const periods = [
    ...new Set(rows.map((r) => asString(r[periodKey])).filter((v): v is string => v !== null)),
  ];
  const categories = [
    ...new Set(rows.map((r) => asString(r[categoryKey])).filter((v): v is string => v !== null)),
  ];
  const cell = new Map<string, number | null>();
  for (const row of rows) {
    const category = asString(row[categoryKey]);
    const period = asString(row[periodKey]);
    if (category && period) {
      cell.set(`${category}|${period}`, asNumber(row[valueKey]));
    }
  }

  return (
    <div className="mt-3 overflow-x-auto rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
              {categoryKey}
            </th>
            {periods.map((period) => (
              <th
                key={period}
                className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]"
              >
                {period}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {categories.map((category) => (
            <tr key={category}>
              <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                {category}
              </td>
              {periods.map((period) => (
                <td
                  key={period}
                  className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums"
                >
                  {formatNumber(cell.get(`${category}|${period}`) ?? null, locale)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FlatSheet({
  rows,
  locale,
}: {
  rows: Record<string, unknown>[];
  locale: string;
}): ReactNode {
  const columns = columnsFor(rows);
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
          {rows.slice(0, 50).map((row, i) => (
            <tr key={columns.map((col) => String(row[col] ?? "")).join("|") || i}>
              {columns.map((col) => {
                const value = row[col];
                const numeric = asNumber(value);
                return (
                  <td
                    key={col}
                    className={
                      numeric !== null && typeof value === "number"
                        ? "border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums"
                        : "border-[var(--border)] border-b px-3 py-2 text-[var(--text)]"
                    }
                  >
                    {typeof value === "number"
                      ? formatNumber(value, locale)
                      : (asString(value) ?? "—")}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function columnsFor(rows: Record<string, unknown>[]): string[] {
  const columns = new Set<string>();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (
        !PLUMBING_KEYS.has(key) &&
        (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
      ) {
        columns.add(key);
      }
    }
  }
  return [...columns];
}

function firstPresentKey(rows: Record<string, unknown>[], candidates: string[]): string | null {
  const first = rows[0];
  if (!first) {
    return null;
  }
  return candidates.find((key) => key in first) ?? null;
}

function extractRows(data: unknown): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}
