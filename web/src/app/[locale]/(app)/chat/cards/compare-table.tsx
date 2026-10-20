"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { fieldLabel } from "@/lib/field-labels";
import { asArray, asNumber, asString, formatNumber, PLUMBING_KEYS } from "@/lib/format-number";

/** 2+ named entities compared on the same fields (`fund_comparison`'s per-symbol rows,
 * `etf_overlap`'s `pairwise_overlap` matrix). Both shapes reduce to "one row per entity, one
 * column per compared field" — `fund_comparison`'s `rows` list already is that; `etf_overlap`'s
 * `pairwise_overlap` list is rendered as a pair × metric table instead, since there's no single
 * entity-per-row for a pairwise structure. */
export function CompareTable({ data }: Readonly<{ data: unknown }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  if (!data || typeof data !== "object") {
    return null;
  }
  const record = data as Record<string, unknown>;

  if (Array.isArray(record.rows)) {
    return <EntityRows rows={asArray(record.rows)} locale={locale} t={t} />;
  }
  if (Array.isArray(record.pairwise_overlap)) {
    return <PairwiseRows rows={asArray(record.pairwise_overlap)} locale={locale} t={t} />;
  }
  return null;
}

function EntityRows({
  rows,
  locale,
  t,
}: Readonly<{
  rows: Record<string, unknown>[];
  locale: string;
  t: ReturnType<typeof useTranslations>;
}>): ReactNode {
  if (rows.length === 0) {
    return null;
  }
  const columns = columnsFor(rows);
  const nameKey = columns.find((c) => c === "symbol") ?? columns[0];

  return (
    <div className="surface-card mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            {columns.map((col) => (
              <th
                key={col}
                className={
                  col === nameKey
                    ? "border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]"
                    : "border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]"
                }
              >
                {fieldLabel(t, col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={(nameKey && asString(row[nameKey])) ?? i}>
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
                    {formatCompareCell(value, locale)}
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

function formatCompareCell(value: unknown, locale: string): string {
  if (typeof value === "number") return formatNumber(asNumber(value), locale);
  if (Array.isArray(value)) return value.map((v) => asString(v) ?? String(v)).join(", ");
  return asString(value) ?? "—";
}

function PairwiseRows({
  rows,
  locale,
  t,
}: Readonly<{
  rows: Record<string, unknown>[];
  locale: string;
  t: ReturnType<typeof useTranslations>;
}>): ReactNode {
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="surface-card mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
              {fieldLabel(t, "pair")}
            </th>
            <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
              {fieldLabel(t, "overlap_pct")}
            </th>
            <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
              {fieldLabel(t, "shared_holding_count")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${asString(row.etf_a) ?? i}|${asString(row.etf_b) ?? i}`}>
              <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                {asString(row.etf_a)} / {asString(row.etf_b)}
              </td>
              <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                {(() => {
                  const pct = asNumber(row.overlap_pct);
                  return pct === null
                    ? "—"
                    : `${formatNumber(pct * 100, locale, { decimals: 1 })}%`;
                })()}
              </td>
              <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                {formatNumber(asNumber(row.shared_holding_count), locale)}
              </td>
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
