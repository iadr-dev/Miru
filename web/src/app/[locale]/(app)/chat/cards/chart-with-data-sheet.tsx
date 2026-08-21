"use client";

import { CaretDownIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  asNumber,
  asString,
  formatNumber,
  PLUMBING_KEYS,
} from "@/app/[locale]/(app)/chat/cards/card-format";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { TableWithActions } from "@/app/[locale]/(app)/chat/table-actions";
import { cn } from "@/lib/cn";

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
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4">
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
                      className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]"
                    >
                      {fieldLabel(t, col)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, ROW_LIMIT).map((row, i) => (
                  <tr key={columns.map((col) => String(row[col] ?? "")).join("|") || i}>
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
                            : (asString(value) ?? "—")}
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
