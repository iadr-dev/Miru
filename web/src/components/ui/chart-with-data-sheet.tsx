"use client";

import { CaretDownIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { TableWithActions } from "@/components/ui/table-actions";
import { cn } from "@/lib/cn";
import { fieldLabel } from "@/lib/field-labels";
import { asNumber, asString, formatNumber, PLUMBING_KEYS } from "@/lib/format-number";

const ROW_LIMIT = 100;

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

/** A column renders right-aligned (numbers, incl. `—` gaps) unless some row holds a real string
 * value for it — a genuinely mixed/text column stays left-aligned. The header cell must match
 * this exactly, not default to `text-left`: a live screenshot caught headers sitting flush-left
 * over right-aligned numeric columns below them, visibly unaligned. */
function isNumericColumn(rows: Record<string, unknown>[], col: string): boolean {
  return !rows.some((row) => typeof row[col] === "string");
}

function cellText(value: unknown, locale: string, numeric: boolean): string {
  if (typeof value === "number") return formatNumber(asNumber(value), locale);
  if (numeric) return "—";
  return asString(value) ?? "—";
}

/** Wraps a chart with its own underlying rows, shown as a collapsible data table directly below
 * it in the same card — a chart alone leaves the exact numbers behind a hover tooltip; the
 * expandable table (same `TableWithActions` copy/download/expand row every other rich-card table
 * gets) keeps them one click away, right next to the visualization they describe, rather than a
 * separately-rendered card the user has to hunt for. Every chart-shaped card (`TimeseriesChart`,
 * `BarChart`, `GroupedBarChart`, `DivergingBarChart`, `FlowDistribution`) wraps its plot in this,
 * passing the same row list it already extracted for the chart itself — one extraction, two
 * views, never a second independent parse of `card.data`. */
export function ChartWithDataSheet({
  rows,
  children,
}: {
  rows: Record<string, unknown>[];
  children: ReactNode;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const [open, setOpen] = useState(false);
  const columns = columnsFor(rows);

  return (
    <div className="surface-card mt-3 p-4">
      {children}
      {rows.length > 0 && columns.length > 0 ? (
        <div className="mt-3 border-[var(--border)] border-t pt-3">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className={cn(
              "inline-flex min-h-9 items-center gap-1 text-[13px] text-[var(--text-muted)]",
              "transition-colors hover:text-[var(--text)]",
            )}
          >
            <CaretDownIcon
              size={14}
              weight="regular"
              aria-hidden
              className={cn("transition-transform", open && "rotate-180")}
            />
            {open ? t("hideDataSheet") : t("showDataSheet")}
          </button>
          {open ? (
            <TableWithActions className="mt-2 text-[13px]">
              <thead>
                <tr className="bg-[var(--surface-2)]">
                  {columns.map((col) => (
                    <th
                      key={col}
                      className={cn(
                        "border-[var(--border)] border-b px-3 py-2 font-medium text-[var(--text-muted)]",
                        isNumericColumn(rows, col) ? "text-right" : "text-left",
                      )}
                    >
                      {fieldLabel(t, col)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, ROW_LIMIT).map((row, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows are a fixed, never-reordered snapshot, and two different days can carry identical values (a flat valuation series), so the row's content alone is not a unique key.
                  <tr key={`${i}|${columns.map((col) => String(row[col] ?? "")).join("|")}`}>
                    {columns.map((col) => {
                      const value = row[col];
                      const numeric = isNumericColumn(rows, col);
                      return (
                        <td
                          key={col}
                          className={cn(
                            "border-[var(--border)] border-b px-3 py-2 text-[var(--text)]",
                            numeric ? "text-right font-num tabular-nums" : "text-left",
                          )}
                        >
                          {cellText(value, locale, numeric)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </TableWithActions>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
