"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { HolderAccumulationRow, HolderThreshold, HolderWindow } from "@/types/market";

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_1.1fr_0.9fr]";
const THRESHOLDS = [200, 400, 800, 1000] as const satisfies readonly HolderThreshold[];
const WINDOWS = [1, 2, 3, 4] as const satisfies readonly HolderWindow[];
// Same collapsed-row-count convention `LeaderboardCard` uses — 10 rows visible inline, "Show all"
// opens the full ranking in a modal rather than growing this card past its neighbors' height.
const COLLAPSED_ROW_COUNT = 10;

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
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
                    ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
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
                    ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
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
 * `LeaderboardRows` so the two never drift in columns/formatting. */
function HolderAccumulationRows({
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
          "grid items-center gap-2.5 border-b border-[var(--border)] pb-1.5 text-[11px] text-[var(--text-muted)]",
          ROW_GRID_COLS,
        )}
      >
        <span />
        <span>{tLeaderboard("columnName")}</span>
        <span className="text-right">{t("custodyPercent")}</span>
        <span className="text-right">{t("sharesChange")}</span>
        <span className="text-right">{t("percentChange")}</span>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {rows.map((row, index) => {
          const up = row.percent_of_custody_change >= 0;
          const color = up ? "var(--up)" : "var(--down)";
          return (
            <li
              key={row.symbol}
              role="group"
              aria-label={`${row.stock_name} ${row.symbol} ${up ? "▲" : "▼"} ${formatNumber(Math.abs(row.percent_of_custody_change), "zh-TW", { decimals: 2 })} pp`}
              className={cn("grid items-center gap-2.5 py-1.5", ROW_GRID_COLS)}
            >
              <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
                {index + 1}
              </span>
              <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
                {row.stock_name}
                <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
                {row.latest_close !== null ? (
                  <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
                    {formatNumber(row.latest_close, "zh-TW", { decimals: 1 })}
                  </span>
                ) : null}
              </span>
              <span aria-hidden className="text-right font-num text-[12px] tabular-nums">
                <span className="text-[var(--text-muted)]">
                  {row.percent_of_custody_before.toFixed(2)}%
                </span>
                <span className="text-[var(--text-muted)]"> → </span>
                <span style={{ color }}>{row.percent_of_custody_after.toFixed(2)}%</span>
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12px] tabular-nums"
                style={{ color }}
              >
                {up ? "+" : ""}
                {formatNumber(row.shares_change / 1000, "zh-TW", { decimals: 1 })}萬張
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[13px] tabular-nums"
                style={{ color }}
              >
                {up ? "▲" : "▼"}
                {formatNumber(Math.abs(row.percent_of_custody_change), "zh-TW", { decimals: 2 })}%
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
