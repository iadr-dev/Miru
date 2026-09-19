"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import type { LeaderboardDirection, MomentumPeriod, PriceMomentumRow } from "@/types/market";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_0.8fr_0.9fr]";
const PERIODS = ["day", "week", "month"] as const satisfies readonly MomentumPeriod[];
// Same collapsed-row-count convention `LeaderboardCard`/`HolderAccumulationCard` use — 10 rows
// visible inline, "Show all" opens the full ranking (up to the backend's 50 cap) in a modal.
const COLLAPSED_ROW_COUNT = 10;

/** 強勢股/弱勢股 — a real single-factor price-momentum ranking (`get_price_momentum`, pure
 * close-to-close %-change over a chosen day/week/month lookback), intentionally distinct from
 * the AI multi-factor composite leaderboard shown elsewhere on this page: this card answers "who
 * moved the most over this period", not "which stocks score well across momentum/trend/value/
 * quality/revenue factors". Row shape follows the same base raw-data model every symbol list on
 * this page uses (`useSymbolQuotes`: name/price/chg%/sparkline) — `pct_change` itself still
 * comes straight from `get_price_momentum` (the real period return the ranking is sorted by),
 * never recomputed from the sparkline's own shorter default window. The 多/空 toggle switches
 * between `top_gainers` (real top-N by return) and `top_losers` (bottom-N), mirroring the AI
 * leaderboard's own bullish/bearish toggle convention; the 日/週/月 toggle re-fetches
 * `get_price_momentum` with a different `period`, owned by the caller (same lift-state-up
 * pattern as `direction`) so the query key/cache entry stays one value with one owner. */
export function PriceMomentumCard({
  gainers,
  losers,
  direction,
  onDirectionChange,
  period,
  onPeriodChange,
  market,
}: {
  gainers: readonly PriceMomentumRow[];
  losers: readonly PriceMomentumRow[];
  direction: LeaderboardDirection;
  onDirectionChange: (direction: LeaderboardDirection) => void;
  period: MomentumPeriod;
  onPeriodChange: (period: MomentumPeriod) => void;
  market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.priceMomentum");
  const tLeaderboard = useTranslations("market.cards.leaderboard");
  const [expanded, setExpanded] = useState(false);
  const rows = direction === "bullish" ? gainers : losers;
  const previewRows = rows.slice(0, COLLAPSED_ROW_COUNT);
  const { quotes: previewQuotes } = useSymbolQuotes(
    previewRows.map((r) => r.symbol),
    market,
  );
  const { quotes: allQuotes } = useSymbolQuotes(expanded ? rows.map((r) => r.symbol) : [], market);
  const previewQuoteBySymbol = new Map(previewQuotes.map((q) => [q.code, q]));
  const allQuoteBySymbol = new Map(allQuotes.map((q) => [q.code, q]));

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <div className="flex shrink-0 items-center gap-2">
          <div className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]">
            {PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onPeriodChange(p)}
                aria-pressed={period === p}
                className={cn(
                  "rounded-[var(--radius-pill)] px-2.5 py-1 text-[12px] transition-colors",
                  period === p
                    ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(p === "day" ? "periodDay" : p === "week" ? "periodWeek" : "periodMonth")}
              </button>
            ))}
          </div>
          <div className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]">
            {(["bullish", "bearish"] as const).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => onDirectionChange(d)}
                aria-pressed={direction === d}
                className={cn(
                  "rounded-[var(--radius-pill)] px-3.5 py-1 text-[12px] transition-colors",
                  direction === d
                    ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(d === "bullish" ? "gainers" : "losers")}
              </button>
            ))}
          </div>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>
      ) : (
        <PriceMomentumRows
          rows={previewRows}
          quoteBySymbol={previewQuoteBySymbol}
          market={market}
        />
      )}

      {/* `mt-auto` pins this button to the card's bottom edge — see `LeaderboardCard`'s matching
       * comment on its own footer group for why (equal-height grid cell in `market-panel.tsx`). */}
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
            <span>{tLeaderboard("expandTo", { count: rows.length })}</span>
          </button>
        ) : null}
      </div>

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={tLeaderboard("collapse")}
        onClose={() => setExpanded(false)}
      >
        <PriceMomentumRows rows={rows} quoteBySymbol={allQuoteBySymbol} market={market} />
      </ExpandModal>
    </section>
  );
}

/** The header row + data rows, shared verbatim between the card's inline preview and the full
 * list inside `ExpandModal` — one render path, built on `SymbolQuoteRows`' shared base. Unlike
 * `LeaderboardRows`, this ranking has no extra column at all — `pct_change` itself is the row's
 * own real period return (`get_price_momentum`), passed via `changePctFor` so it's used instead
 * of the quote's own shorter default-window `changePct`; `stock_name` is the row's own real name
 * from that same source, passed as `name` so it's preferred over the roster-derived one. */
function PriceMomentumRows({
  rows,
  quoteBySymbol,
  market,
}: {
  rows: readonly PriceMomentumRow[];
  quoteBySymbol: ReadonlyMap<string, ReturnType<typeof useSymbolQuotes>["quotes"][number]>;
  market: Market;
}): ReactNode {
  return (
    <SymbolQuoteRows
      rows={rows.map((row) => ({ ...row, name: row.stock_name }))}
      quoteBySymbol={quoteBySymbol}
      market={market}
      gridCols={ROW_GRID_COLS}
      showRank
      changePctFor={(row) => row.pct_change}
    />
  );
}
