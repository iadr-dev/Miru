"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { LockedRowsNote } from "@/components/market/locked-rows-note";
import { SymbolLink } from "@/components/market/symbol-link";
import { WatchlistStarButton } from "@/components/stock/watchlist-star-button";
import { ExpandModal } from "@/components/ui/expand-modal";
import { SignedChange } from "@/components/ui/signed-change";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { directionColor, formatSignedChange } from "@/lib/signed";
import type { HolderAccumulationRow, HolderThreshold, HolderWindow } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_1.1fr_0.9fr_28px]";
const THRESHOLDS = [200, 400, 800, 1000] as const satisfies readonly HolderThreshold[];
const WINDOWS = [1, 2, 3, 4] as const satisfies readonly HolderWindow[];
// Same collapsed-row-count convention `LeaderboardCard` uses — 15 rows visible inline, "Show all"
// opens the full ranking in a modal rather than growing this card past its neighbors' height.
const COLLAPSED_ROW_COUNT = 15;

const THRESHOLD_KEYS: Record<HolderThreshold, string> = {
  200: "threshold200",
  400: "threshold400",
  800: "threshold800",
  1000: "threshold1000",
};
const WINDOW_KEYS: Record<HolderWindow, string> = {
  1: "window1",
  2: "window2",
  3: "window3",
  4: "window4",
};

/** 大戶加碼股 — the whole-market ranking of which stocks grew a chosen large-holder tier
 * (>=200/400/800/1,000張) share-of-custody the most over 1-4 weekly `get_holder_accumulation`
 * snapshots. Threshold and window are both owned by the caller (`market-panel.tsx`), the same
 * lift-state-up pattern `PriceMomentumCard`'s period/direction toggles use, so the query key
 * stays one value with one owner. Row shape intentionally does NOT follow the base raw-data
 * model (name/price/chg%/sparkline) every other symbol list on this page uses — sparkline/chg%
 * data means "today's price move", not "this tier's multi-week share-of-custody change", which
 * is the actual thing being ranked here; `latest_close` (a plain price, no sparkline) is shown
 * only for context. */
export function HolderAccumulationCard({
  rows,
  threshold,
  onThresholdChange,
  window,
  onWindowChange,
  asOf,
  periodAgo,
}: {
  rows: readonly HolderAccumulationRow[];
  threshold: HolderThreshold;
  onThresholdChange: (threshold: HolderThreshold) => void;
  window: HolderWindow;
  onWindowChange: (window: HolderWindow) => void;
  asOf: string | null;
  periodAgo: string | null;
}): ReactNode {
  const t = useTranslations("market.cards.holderAccumulation");
  const tLeaderboard = useTranslations("market.cards.leaderboard");
  const [expanded, setExpanded] = useState(false);
  const previewRows = rows.slice(0, COLLAPSED_ROW_COUNT);

  return (
    <section className="flex w-full flex-col surface-card p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]">
            {THRESHOLDS.map((th) => (
              <button
                key={th}
                type="button"
                onClick={() => onThresholdChange(th)}
                aria-pressed={threshold === th}
                className={cn(
                  "rounded-[var(--radius-pill)] px-2.5 py-1 text-[12px] transition-colors",
                  threshold === th
                    ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(THRESHOLD_KEYS[th])}
              </button>
            ))}
          </div>
          <div className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]">
            {WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => onWindowChange(w)}
                aria-pressed={window === w}
                className={cn(
                  "rounded-[var(--radius-pill)] px-2.5 py-1 text-[12px] transition-colors",
                  window === w
                    ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(WINDOW_KEYS[w])}
              </button>
            ))}
          </div>
        </div>
      </div>

      {asOf && periodAgo ? (
        <p className="mb-2 text-[11px] text-[var(--text-muted)]">
          {periodAgo} → {asOf}
        </p>
      ) : null}

      {rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>
      ) : (
        <HolderAccumulationRows rows={previewRows} t={t} tLeaderboard={tLeaderboard} />
      )}

      {/* `mt-auto` pins this footer group to the card's bottom edge — see `LeaderboardCard`'s
       * matching comment on its own footer group for why (equal-height grid cell in
       * `market-panel.tsx`). */}
      <div className="mt-auto">
        {rows.length > COLLAPSED_ROW_COUNT ? (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className={cn(
              "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
              "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
            )}
          >
            <span>{t("expandTo", { count: rows.length })}</span>
          </button>
        ) : null}

        {rows.length > 0 ? (
          <p className="mt-3 text-[11px] text-[var(--text-muted)]">{t("updateCadence")}</p>
        ) : null}
      </div>

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <HolderAccumulationRows rows={rows} t={t} tLeaderboard={tLeaderboard} />
      </ExpandModal>
    </section>
  );
}

/** The header row + data rows, shared verbatim between the card's inline preview and the full
 * list inside `ExpandModal` — one render path, mirroring `LeaderboardCard`'s own
 * `LeaderboardRows` so the two never drift in columns/formatting. Exported so
 * `TwRankingsCard`'s own 大戶買賣 tab (`tw-rankings-card.tsx`) reuses this exact row rendering
 * rather than duplicating it — that tab's own docstring explains why `HolderAccumulationCard`'s
 * bespoke row shape (not `SymbolQuoteRows`-based) is reused as-is. */
export function HolderAccumulationRows({
  rows,
  t,
  tLeaderboard,
}: {
  rows: readonly HolderAccumulationRow[];
  t: ReturnType<typeof useTranslations>;
  tLeaderboard: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <>
      <div
        aria-hidden
        className={cn(
          "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
          ROW_GRID_COLS,
        )}
      >
        <span />
        <span>{tLeaderboard("columnName")}</span>
        <span className="text-right">{t("custodyPercent")}</span>
        <span className="text-right">{t("sharesChange")}</span>
        <span className="text-right">{t("percentChange")}</span>
        <span />
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {rows.map((row) =>
          row.locked ? (
            <LockedHolderAccumulationRow key={row.rank} rank={row.rank} />
          ) : (
            <HolderAccumulationRowItem key={row.symbol} row={row} />
          ),
        )}
      </ul>
      <LockedRowsNote rows={rows} />
    </>
  );
}

/** governance.md §4 top-N-preview exception's row-level lock: every field but `rank` is nulled
 * server-side for a `locked` row — mirrors `daily-signal-table.tsx`'s own locked-row pattern for
 * this card's 5-column grid. */
function LockedHolderAccumulationRow({ rank }: { readonly rank: number }): ReactNode {
  return (
    <li className={cn("grid items-center gap-2.5 py-1.5", ROW_GRID_COLS)}>
      <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
        {rank}
      </span>
      <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
        <LockIcon size={11} weight="regular" aria-hidden />
        <span aria-hidden>••••</span>
      </span>
      <span aria-hidden className="text-right text-[12px] text-[var(--text-muted)]">
        ••••
      </span>
      <span aria-hidden className="text-right text-[12px] text-[var(--text-muted)]">
        ••••
      </span>
      <span aria-hidden className="text-right text-[13px] text-[var(--text-muted)]">
        ••••
      </span>
      <span aria-hidden />
    </li>
  );
}

/** One real (never-locked) row — split out of `HolderAccumulationRows` so that component's own
 * `.map` stays a simple locked/unlocked branch. */
function HolderAccumulationRowItem({ row }: { readonly row: HolderAccumulationRow }): ReactNode {
  const change = row.percent_of_custody_change ?? 0;
  const color = directionColor(change, 2);
  return (
    <li
      role="group"
      aria-label={`${row.stock_name} ${row.symbol} ${formatSignedChange(change, "zh-TW", { decimals: 2, unit: " pp" })}`}
      className={cn("grid items-center gap-2.5 py-1.5", ROW_GRID_COLS)}
    >
      <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
        {row.rank}
      </span>
      <SymbolLink
        symbol={row.symbol ?? ""}
        market="TW"
        className="truncate text-[13px] text-[var(--text)] hover:underline"
      >
        {row.stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
        {row.latest_close !== null ? (
          <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
            {formatNumber(row.latest_close, "zh-TW", { decimals: 1 })}
          </span>
        ) : null}
      </SymbolLink>
      <span aria-hidden className="text-right font-num text-[12px] tabular-nums">
        <span className="text-[var(--text-muted)]">
          {(row.percent_of_custody_before ?? 0).toFixed(2)}%
        </span>
        <span className="text-[var(--text-muted)]"> → </span>
        <span style={{ color }}>{(row.percent_of_custody_after ?? 0).toFixed(2)}%</span>
      </span>
      <span aria-hidden className="text-right font-num text-[12px] tabular-nums" style={{ color }}>
        {(row.shares_change ?? 0) > 0 ? "+" : ""}
        {formatNumber((row.shares_change ?? 0) / 1000, "zh-TW", { decimals: 1 })}萬張
      </span>
      <SignedChange
        value={change}
        decimals={2}
        unit="%"
        spaced={false}
        locale="zh-TW"
        labelled={false}
        className="text-right text-[13px]"
      />
      {row.symbol ? (
        <WatchlistStarButton symbol={row.symbol} market="TW" assetClass="stock" size="compact" />
      ) : (
        <span aria-hidden />
      )}
    </li>
  );
}
