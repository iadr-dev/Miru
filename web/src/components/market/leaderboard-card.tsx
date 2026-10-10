"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import { PREVIEW_ROWS } from "@/lib/density";
import type { LeaderboardDirection, ScoreBand, ScoreResult } from "@/types/market";
import type { Market } from "@/types/settings";
import type { WatchlistAssetClass } from "@/types/watchlist";

const COLLAPSED_ROW_COUNT = PREVIEW_ROWS;

const BAND_TOKEN: Record<ScoreBand, string> = {
  偏多: "var(--up)",
  中性: "var(--text-muted)",
  偏空: "var(--down)",
};

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_0.8fr_0.9fr_0.9fr_1.3fr_28px]";

/**
 * AI 多空排行 — the real `get_score_leaderboard` composite ranking, enriched via
 * `useSymbolQuotes` (two batched, ≤20-symbol real backend calls: `symbol-info` for the name,
 * `price-history` for price/chg%/sparkline). `ScoreResult` itself only carries `symbol`; every
 * enrichment field renders "—"/nothing rather than a fabricated value when its own fetch doesn't
 * cover that symbol (e.g. a delisted or newly-listed one absent from the roster). `direction`
 * toggles the real API query (bullish/bearish), never a client-side re-sort of one fetched
 * direction. "展開完整" opens the full list in a modal (`ExpandModal`) rather than growing this
 * card inline — keeps the card's own height stable regardless of result count. The stock/ETF
 * universe toggle that used to live here moved out once the ETF tab got its own leaderboard
 * sub-tab (`EtfLeaderboardPanel`) — each caller now owns a fixed universe, so this card no
 * longer renders a universe switcher at all. */
export function LeaderboardCard({
  results,
  asOf,
  direction,
  onDirectionChange,
  market,
  assetClass,
}: {
  results: readonly ScoreResult[];
  asOf: string | null;
  direction: LeaderboardDirection;
  onDirectionChange: (direction: LeaderboardDirection) => void;
  market: Market;
  /** Fixed per caller — the stock leaderboard (`DailyFocusPanel`) always passes `"stock"`, the
   * ETF leaderboard (`EtfLeaderboardPanel`) always passes `"etf"`; this card no longer renders a
   * universe switcher (see docstring above), so it's a caller-supplied constant, never derived. */
  assetClass: WatchlistAssetClass;
}): ReactNode {
  const t = useTranslations("market.cards.leaderboard");
  const [expanded, setExpanded] = useState(false);
  const previewResults = results.slice(0, COLLAPSED_ROW_COUNT);
  const { quotes: previewQuotes } = useSymbolQuotes(
    previewResults.map((r) => r.symbol),
    market,
  );
  const { quotes: allQuotes } = useSymbolQuotes(
    expanded ? results.map((r) => r.symbol) : [],
    market,
  );
  const previewQuoteBySymbol = new Map(previewQuotes.map((q) => [q.code, q]));
  const allQuoteBySymbol = new Map(allQuotes.map((q) => [q.code, q]));

  return (
    <section className="flex w-full flex-col surface-card p-4 sm:p-5">
      <div className="mb-1 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">{t("subtitle")}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
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
                    ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(d === "bullish" ? "bullish" : "bearish")}
              </button>
            ))}
          </div>
        </div>
      </div>

      {results.length === 0 ? (
        <p className="mt-4 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <LeaderboardRows
          results={previewResults}
          quoteBySymbol={previewQuoteBySymbol}
          market={market}
          assetClass={assetClass}
          t={t}
        />
      )}

      {/* `mt-auto` pins this footer group to the card's bottom edge regardless of row count, so
       * this card's bottom aligns with its shorter/taller neighbor's bottom in the xl:grid-cols
       * layout (`items-stretch` in `top-split.tsx` gives both cells the same height; this is what
       * makes THIS card's own trailing content reach that shared bottom edge). */}
      <div className="mt-auto">
        {results.length > COLLAPSED_ROW_COUNT ? (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className={cn(
              "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
              "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
            )}
          >
            <span>{t("expandTo", { count: results.length })}</span>
          </button>
        ) : null}

        {results.length > 0 ? (
          <p className="mt-2 text-[11px] text-[var(--text-muted)]">{t("priceNotBasis")}</p>
        ) : null}
        {asOf ? (
          <p className="mt-2 text-[11px] text-[var(--text-muted)]">
            {t("asOf", { date: asOf })} · {t("nextUpdate")}
          </p>
        ) : null}
      </div>

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <LeaderboardRows
          results={results}
          quoteBySymbol={allQuoteBySymbol}
          market={market}
          assetClass={assetClass}
          t={t}
        />
      </ExpandModal>
    </section>
  );
}

/** The header row + data rows, shared verbatim between the card's inline preview and the full
 * list inside `ExpandModal` — one render path, so the two never drift in columns/formatting.
 * Built on `SymbolQuoteRows`' shared 4-column base, adding only the one column this ranking
 * has that a plain symbol list doesn't: the AI composite-score pill. Click-to-sort on 分數 (via
 * `extraSortable`, same convention 漲跌%/成交量 already use) — a `null` composite (no score for
 * this symbol yet) sorts last regardless of direction, `SymbolQuoteRows`' own null-handling. */
function LeaderboardRows({
  results,
  quoteBySymbol,
  market,
  assetClass,
  t,
}: {
  results: readonly ScoreResult[];
  quoteBySymbol: ReadonlyMap<string, ReturnType<typeof useSymbolQuotes>["quotes"][number]>;
  market: Market;
  assetClass: WatchlistAssetClass;
  t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <SymbolQuoteRows
      rows={results}
      quoteBySymbol={quoteBySymbol}
      market={market}
      assetClass={assetClass}
      gridCols={ROW_GRID_COLS}
      showRank
      extraSortable={{ label: t("columnScore"), valueFor: (result) => result.composite }}
      extraAriaLabel={(result) =>
        `${result.band} ${t("scoreAria")} ${
          result.composite !== null ? formatNumber(result.composite, "zh-TW") : t("empty")
        }`
      }
      extraCell={(result) => (
        <span
          aria-hidden
          className="relative flex h-5 items-center justify-center overflow-hidden rounded-[var(--radius-pill)] bg-[var(--surface-2)]"
        >
          <span
            className="absolute inset-y-0 left-0 rounded-[var(--radius-pill)] opacity-25"
            style={{
              width: `${result.composite ?? 0}%`,
              backgroundColor: BAND_TOKEN[result.band],
            }}
          />
          <span
            className="relative font-bold font-num text-[12px] tabular-nums"
            style={{ color: BAND_TOKEN[result.band] }}
          >
            {result.composite !== null ? formatNumber(result.composite, "zh-TW") : "—"}
          </span>
        </span>
      )}
    />
  );
}
