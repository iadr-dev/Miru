"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { compareRows, DEFAULT_SORT_DIRECTION } from "@/components/market/etf-list-sort";
import { SymbolLink } from "@/components/market/symbol-link";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { EtfListRow, EtfSortKey, SortDirection } from "@/types/market";
import type { Market } from "@/types/settings";

const ETF_ROW_GRID_COLS = "grid-cols-[1.4fr_0.8fr_0.9fr_1fr_0.8fr_1fr_1fr_0.8fr]";
// Same collapsed-row-count convention `HolderAccumulationCard`/`LeaderboardCard` use — enough
// rows to be useful inline while keeping this card's height comparable to its dashboard
// neighbors; "展開完整{count}筆" opens the full sorted list (unbounded, still scrollable) in
// `ExpandModal` instead of growing this card past the rest of the ETF tab.
const COLLAPSED_ROW_COUNT = 10;

// 槓桿/反向 badges use distinct categorical tokens (--chart-1/--chart-2), never --up/--down —
// leveraged/inverse is a fund-structure fact, not a price/%/score signal (web/CLAUDE.md §4).
const SYMBOL_TYPE_BADGE: Record<string, { labelKey: string; color: string }> = {
  etf_leveraged: { labelKey: "leveraged", color: "var(--chart-1)" },
  etf_inverse: { labelKey: "inverse", color: "var(--chart-2)" },
};

// 主動式/被動式 is an orthogonal axis from 原型/槓桿型/反向型 above (a leveraged/inverse ETF is
// passive by construction; a plain "原型" ETF can be either) — its own categorical token
// (--chart-3), never --up/--down, same reasoning as SYMBOL_TYPE_BADGE.
const ACTIVE_BADGE_COLOR = "var(--chart-3)";

/**
 * 主動式ETF-style sortable ETF list — every column's header is a real `<button>` (click to
 * sort, click again to flip direction), matching the reference app's own ▲▼-on-every-column
 * convention. No sortable-column table existed anywhere in this codebase before this one — the
 * mechanism (header buttons + `aria-sort` + a plain ▲/▼ glyph) is built from scratch here,
 * modeled on the existing pill-toggle button styling every other market card already uses.
 *
 * Sort state lives here, but only the top `COLLAPSED_ROW_COUNT` of the sorted list renders
 * inline — "展開完整{count}筆" opens the identical sorted list (unbounded) inside `ExpandModal`,
 * the same collapse-to-N convention `LeaderboardCard`/`HolderAccumulationCard` already use, so
 * this card's height stays comparable to its dashboard neighbors instead of growing to ~300 rows.
 */
export function EtfListTable({
  rows,
  market,
}: {
  readonly rows: readonly EtfListRow[];
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.etfList");
  const locale = useLocale();
  const [sortKey, setSortKey] = useState<EtfSortKey>("aum");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [expanded, setExpanded] = useState(false);

  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => compareRows(a, b, sortKey, sortDirection)),
    [rows, sortKey, sortDirection],
  );

  const toggleSort = (key: EtfSortKey): void => {
    if (key === sortKey) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDirection(DEFAULT_SORT_DIRECTION[key]);
  };

  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const previewRows = sortedRows.slice(0, COLLAPSED_ROW_COUNT);

  return (
    <div>
      <EtfListBody
        rows={previewRows}
        sortKey={sortKey}
        sortDirection={sortDirection}
        onToggleSort={toggleSort}
        market={market}
        locale={locale}
        t={t}
        maxHeightClassName="max-h-[420px]"
      />

      {sortedRows.length > COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{t("expandTo", { count: sortedRows.length })}</span>
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <EtfListBody
          rows={sortedRows}
          sortKey={sortKey}
          sortDirection={sortDirection}
          onToggleSort={toggleSort}
          market={market}
          locale={locale}
          t={t}
          maxHeightClassName="max-h-[640px]"
        />
      </ExpandModal>
    </div>
  );
}

/** The scrollable header+rows block, shared verbatim between the card's inline preview and the
 * full list inside `ExpandModal` — one render path, so the two never drift in columns/sort
 * behavior. `maxHeightClassName` differs only because the modal has more vertical room than the
 * inline card does. */
function EtfListBody({
  rows,
  sortKey,
  sortDirection,
  onToggleSort,
  market,
  locale,
  t,
  maxHeightClassName,
}: {
  readonly rows: readonly EtfListRow[];
  readonly sortKey: EtfSortKey;
  readonly sortDirection: SortDirection;
  readonly onToggleSort: (key: EtfSortKey) => void;
  readonly market: Market;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
  readonly maxHeightClassName: string;
}): ReactNode {
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[820px]">
        <EtfListHeaderRow
          sortKey={sortKey}
          sortDirection={sortDirection}
          onToggleSort={onToggleSort}
          t={t}
        />
        <ul className={cn("divide-y divide-[var(--border)] overflow-y-auto", maxHeightClassName)}>
          {rows.map((row) => (
            <EtfListRowItem key={row.symbol} row={row} market={market} locale={locale} t={t} />
          ))}
        </ul>
      </div>
    </div>
  );
}

function EtfListHeaderRow({
  sortKey,
  sortDirection,
  onToggleSort,
  t,
}: {
  readonly sortKey: EtfSortKey;
  readonly sortDirection: SortDirection;
  readonly onToggleSort: (key: EtfSortKey) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const columns: Array<{ key: EtfSortKey; labelKey: string; align: "left" | "right" }> = [
    { key: "name", labelKey: "columnName", align: "left" },
    { key: "close", labelKey: "columnPrice", align: "right" },
    { key: "change_pct", labelKey: "columnChange", align: "right" },
    { key: "turnover", labelKey: "columnVolume", align: "right" },
    { key: "return_1w_pct", labelKey: "columnWeeklyReturn", align: "right" },
    { key: "distribution_yield_ttm_pct", labelKey: "columnYield", align: "right" },
    { key: "aum", labelKey: "columnAum", align: "right" },
    { key: "expense_ratio_pct_approx", labelKey: "columnExpense", align: "right" },
  ];

  return (
    <div
      className={cn(
        "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5",
        ETF_ROW_GRID_COLS,
      )}
    >
      {columns.map((col) => {
        const active = sortKey === col.key;
        const ariaSort = active ? (sortDirection === "asc" ? "ascending" : "descending") : "none";
        return (
          // aria-sort is only valid on a columnheader-role element, not on the interactive
          // <button> itself — this grid isn't a real <table>, so the role is applied by hand.
          <div key={col.key} role="columnheader" aria-sort={ariaSort} tabIndex={-1}>
            <button
              type="button"
              onClick={() => onToggleSort(col.key)}
              className={cn(
                "flex min-h-11 w-full items-center gap-1 text-[11px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]",
                col.align === "right" && "justify-end",
                active && "text-[var(--text)]",
              )}
            >
              <span>{t(col.labelKey)}</span>
              {active ? (
                // Sort caret is chrome, not price/%/score data — never `--up`/`--down`
                // (web/CLAUDE.md §4 reserves those tokens for real 紅漲綠跌 signals).
                <span aria-hidden className="text-[10px] text-[var(--text-muted)]">
                  {sortDirection === "asc" ? "▲" : "▼"}
                </span>
              ) : null}
            </button>
          </div>
        );
      })}
    </div>
  );
}

function EtfListRowItem({
  row,
  market,
  locale,
  t,
}: {
  readonly row: EtfListRow;
  readonly market: Market;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const up = (row.change_pct ?? 0) >= 0;
  const color = row.change_pct === null ? "var(--text-muted)" : up ? "var(--up)" : "var(--down)";
  const badge = SYMBOL_TYPE_BADGE[row.symbol_type];

  return (
    <li
      role="group"
      aria-label={`${row.name} ${row.symbol} ${
        row.change_pct !== null
          ? `${up ? "▲" : "▼"} ${formatNumber(Math.abs(row.change_pct), locale, { decimals: 2 })}%`
          : t("noChangeData")
      }`}
      className={cn("grid items-center gap-2.5 py-2", ETF_ROW_GRID_COLS)}
    >
      <SymbolLink
        symbol={row.symbol}
        market={market}
        className="truncate text-[13px] text-[var(--text)] hover:underline"
      >
        {row.name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
        {row.is_active_etf ? (
          <span
            className="ml-1.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-1.5 py-0.5 font-medium text-[10px]"
            style={{ color: ACTIVE_BADGE_COLOR }}
          >
            {t("active")}
          </span>
        ) : null}
        {badge ? (
          <span
            className="ml-1.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-1.5 py-0.5 font-medium text-[10px]"
            style={{ color: badge.color }}
          >
            {t(badge.labelKey)}
          </span>
        ) : null}
      </SymbolLink>
      <span aria-hidden className="text-right font-num text-[13px] text-[var(--text)] tabular-nums">
        {row.close !== null ? formatNumber(row.close, locale, { decimals: 2 }) : "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] tabular-nums" style={{ color }}>
        {row.change_pct !== null
          ? `${up ? "▲" : "▼"}${formatNumber(Math.abs(row.change_pct), locale, { decimals: 2 })}%`
          : "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.turnover !== null ? formatCompactNumber(row.turnover, locale) : "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.return_1w_pct !== null
          ? `${row.return_1w_pct >= 0 ? "▲" : "▼"}${formatNumber(Math.abs(row.return_1w_pct), locale, { decimals: 2 })}%`
          : "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.distribution_yield_ttm_pct !== null
          ? `${formatNumber(row.distribution_yield_ttm_pct, locale, { decimals: 2 })}%`
          : "-"}
        {row.distribution_frequency ? (
          <span className="ml-1 text-[10px] text-[var(--text-muted)]">
            ({row.distribution_frequency})
          </span>
        ) : null}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.aum !== null ? formatCompactNumber(row.aum, locale) : "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.expense_ratio_pct_approx !== null
          ? `${formatNumber(row.expense_ratio_pct_approx, locale, { decimals: 2 })}%`
          : "—"}
      </span>
    </li>
  );
}
