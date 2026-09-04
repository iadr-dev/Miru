"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import type { DailySignalRow, HoldingStatus } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.9fr_0.9fr_0.9fr_0.9fr]";

// Each of the 4 real statuses gets its own distinct categorical --chart-* token (never --up/
// --down — this is a fund-structure classification, not a price/%/score signal, per web/CLAUDE.md
// §4). 新增/加碼 share the same "growth" family hue-wise (chart-1/chart-4) and 刪除/減碼 share the
// same "shrink" family (chart-2/chart-3), but each status still renders as a visually distinct
// color — 加碼 and 新增 (or 減碼 and 刪除) must never look identical, since the filter chips let a
// user isolate exactly one status at a time.
const STATUS_TOKEN: Record<HoldingStatus, string> = {
  新增: "var(--chart-1)",
  加碼: "var(--chart-4)",
  刪除: "var(--chart-2)",
  減碼: "var(--chart-3)",
  no_prior_data: "var(--text-muted)",
};

/**
 * 今日訊號 ranked list — one row per stock with a nonzero aggregate holding change today,
 * across every active ETF combined. Unlike `EtfMoneyFlowTable` (a bounded date-range flow
 * ranking), every row here already carries its own 股價/漲跌幅 from the backend (`DailySignalRow.
 * close`/`change_pct`, derived from the same-day `PRICE_EOD_JOB` close) — no `useSymbolQuotes`
 * batch fetch needed, since `get_daily_signal` is already a single-day whole-market read.
 *
 * Filtering by `statusFilter` happens here (not in the caller) so both the inline preview and
 * the `ExpandModal` full list in `DailySignalPanel` see the identical filtered set — the caller
 * only decides how many of THIS component's rows to slice for the preview.
 */
export function DailySignalTable({
  rows,
  statusFilter,
}: {
  readonly rows: readonly DailySignalRow[];
  readonly statusFilter: HoldingStatus | null;
}): ReactNode {
  const t = useTranslations("market.cards.dailySignal");
  const locale = useLocale();
  const filtered = statusFilter ? rows.filter((row) => row.status === statusFilter) : rows;

  if (filtered.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[560px]">
        <div
          aria-hidden
          className={cn(
            "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
            ROW_GRID_COLS,
          )}
        >
          <span>{t("columnStock")}</span>
          <span className="text-right">{t("columnPrice")}</span>
          <span className="text-center">{t("columnStatus")}</span>
          <span className="text-right">{t("columnSharesDelta")}</span>
          <span className="text-right">{t("columnPctChange")}</span>
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {filtered.map((row) => (
            <DailySignalRowItem key={row.component_stock_id} row={row} locale={locale} t={t} />
          ))}
        </ul>
      </div>
    </div>
  );
}

/** How many filtered rows `DailySignalPanel` shows inline before offering "展開完整N筆" —
 * mirrors `HolderAccumulationCard`'s own `COLLAPSED_ROW_COUNT` convention. */
export const DAILY_SIGNAL_COLLAPSED_ROW_COUNT = 10;

/** The same filter this component applies internally, exposed so the caller can compute the
 * true filtered count for its "展開完整{count}筆" label and preview slice without duplicating
 * the filter predicate. */
export function filterDailySignalRows(
  rows: readonly DailySignalRow[],
  statusFilter: HoldingStatus | null,
): readonly DailySignalRow[] {
  return statusFilter ? rows.filter((row) => row.status === statusFilter) : rows;
}

/** A signed value's `--up`/`--down` color, or `--text-muted` when the value itself is `null`
 * (no data) — the one place `DailySignalRowItem`'s per-column sign-to-token mapping lives, so
 * that component's own branching stays under the project's cognitive-complexity budget. */
function signedColor(value: number | null): string {
  if (value === null) return "var(--text-muted)";
  return value >= 0 ? "var(--up)" : "var(--down)";
}

function DailySignalRowItem({
  row,
  locale,
  t,
}: {
  readonly row: DailySignalRow;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const up = (row.change_pct ?? 0) >= 0;
  const color = signedColor(row.change_pct);
  const statusColor = STATUS_TOKEN[row.status];
  const statusLabel = row.status === "no_prior_data" ? t("noPriorData") : row.status;
  // 變動張數/變動幅度 are real signed holdings-change figures (like 股數變化/比例變化 in
  // `HolderAccumulationCard`), not the categorical status classification — they get --up/--down
  // colored by their own sign, distinct from the status pill's fixed STATUS_TOKEN color.
  const sharesDeltaColor =
    row.shares_delta === 0 ? "var(--text-muted)" : signedColor(row.shares_delta);
  const pctUp = (row.pct_change ?? 0) >= 0;
  const pctChangeColor = signedColor(row.pct_change);

  return (
    <li
      role="group"
      aria-label={`${row.component_stock_name} ${row.component_stock_id} ${statusLabel} ${
        row.change_pct !== null
          ? `${up ? "▲" : "▼"} ${formatNumber(Math.abs(row.change_pct), locale, { decimals: 2 })}%`
          : t("noChangeData")
      }`}
      className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
    >
      <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
        {row.component_stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
          {row.component_stock_id}
        </span>
      </span>
      <span aria-hidden className="text-right font-num text-[13px] text-[var(--text)] tabular-nums">
        {row.close !== null ? formatNumber(row.close, locale, { decimals: 2 }) : "—"}
        <span
          className="ml-1 font-num text-[11px] tabular-nums"
          style={{ color: row.change_pct === null ? undefined : color }}
        >
          {row.change_pct !== null
            ? `${up ? "▲" : "▼"}${formatNumber(Math.abs(row.change_pct), locale, { decimals: 2 })}%`
            : ""}
        </span>
      </span>
      <span
        aria-hidden
        className="mx-auto rounded-[var(--radius-pill)] px-2 py-0.5 text-center font-medium text-[11px]"
        style={{
          backgroundColor: `color-mix(in srgb, ${statusColor} 18%, transparent)`,
          color: statusColor,
        }}
      >
        {statusLabel}
      </span>
      <span
        aria-hidden
        className="text-right font-num text-[12px] tabular-nums"
        style={{ color: sharesDeltaColor }}
      >
        {row.shares_delta > 0 ? "+" : ""}
        {formatNumber(row.shares_delta, locale, { decimals: 0 })}
      </span>
      <span
        aria-hidden
        className="text-right font-num text-[12px] tabular-nums"
        style={{ color: pctChangeColor }}
      >
        {row.pct_change !== null
          ? `${pctUp ? "▲" : "▼"}${formatNumber(Math.abs(row.pct_change) * 100, locale, { decimals: 1 })}%`
          : "—"}
      </span>
    </li>
  );
}
