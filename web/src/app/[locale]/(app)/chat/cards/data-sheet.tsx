"use client";

import { CaretDownIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { financialStatementItemLabel } from "@/app/[locale]/(app)/chat/cards/financial-statement-labels";
import { TableWithActions } from "@/components/ui/table-actions";
import { cn } from "@/lib/cn";
import { fieldLabel, valueLabel } from "@/lib/field-labels";
import { asArray, asNumber, asString, formatNumber, PLUMBING_KEYS } from "@/lib/format-number";

/** Rows shown before the "顯示全部" toggle — a flat roster (`insider_shareholding`,
 * `theme_members`) is routinely 30-40+ records where most carry no notable value (e.g. a
 * director with zero pledged shares), so showing every row by default buries the handful that
 * matter under a wall of zeros. Unlike `ChartWithDataSheet` (which can fully hide its table
 * behind a chart), a flat sheet's table IS the card's only content, so collapsed state still
 * shows a short preview rather than nothing. */
const FLAT_SHEET_PREVIEW_ROWS = 8;

/** Line-item rows with a category dimension, often pivoted by period (`financial_statement`'s
 * `item` × `period_end` × `value`) or a flat roster (`insider_shareholding`, `theme_members`).
 * Pivots into a wide table when a period-like field repeats across rows for the same category;
 * otherwise renders one row per record. */
export function DataSheet({ data }: Readonly<{ data: unknown }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
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
        t={t}
      />
    );
  }
  return <FlatSheet rows={rows} locale={locale} t={t} />;
}

function PivotedSheet({
  rows,
  categoryKey,
  periodKey,
  valueKey,
  locale,
  t,
}: Readonly<{
  rows: Record<string, unknown>[];
  categoryKey: string;
  periodKey: string;
  valueKey: string;
  locale: string;
  t: ReturnType<typeof useTranslations>;
}>): ReactNode {
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
    <TableWithActions className="text-[13px]">
      <thead>
        <tr className="bg-[var(--surface-2)]">
          <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
            {fieldLabel(t, categoryKey)}
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
              {categoryKey === "item" ? financialStatementItemLabel(category, locale) : category}
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
    </TableWithActions>
  );
}

/** A text cell: enum-like columns (theme `kind`, supply-chain `tier`) are translated, anything else
 * is shown as-is; an empty cell is a dash. */
function valueText(
  t: ReturnType<typeof useTranslations>,
  column: string,
  value: string | null,
): string {
  return value === null ? "—" : valueLabel(t, column, value);
}

function FlatSheet({
  rows,
  locale,
  t,
}: Readonly<{
  rows: Record<string, unknown>[];
  locale: string;
  t: ReturnType<typeof useTranslations>;
}>): ReactNode {
  const columns = columnsFor(rows);
  const [expanded, setExpanded] = useState(false);
  const hiddenCount = rows.length - FLAT_SHEET_PREVIEW_ROWS;
  const visibleRows = expanded ? rows.slice(0, 50) : rows.slice(0, FLAT_SHEET_PREVIEW_ROWS);

  return (
    <>
      <TableWithActions className="text-[13px]">
        <thead>
          <tr className="bg-[var(--surface-2)]">
            {columns.map((col) => (
              <th
                key={col}
                className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]"
              >
                {fieldLabel(t, col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: a fixed, never-reordered preview of the rows; identical rows (e.g. a flat series) must not share a key.
            <tr key={`${i}|${columns.map((col) => String(row[col] ?? "")).join("|")}`}>
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
                      : valueText(t, col, asString(value))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </TableWithActions>
      {hiddenCount > 0 ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className={cn(
            "mt-2 inline-flex min-h-9 items-center gap-1 text-[13px] text-[var(--text-muted)]",
            "transition-colors hover:text-[var(--text)]",
          )}
        >
          <CaretDownIcon
            size={14}
            weight="regular"
            aria-hidden
            className={cn("transition-transform", expanded && "rotate-180")}
          />
          {expanded ? t("showFewerRows") : t("showAllRows", { count: hiddenCount })}
        </button>
      ) : null}
    </>
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
