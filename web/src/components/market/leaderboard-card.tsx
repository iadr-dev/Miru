"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { Sparkline } from "@/components/market/sparkline";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import type { LeaderboardDirection, ScoreBand, ScoreResult } from "@/types/market";
import type { Market } from "@/types/settings";

const COLLAPSED_ROW_COUNT = 13;

const BAND_TOKEN: Record<ScoreBand, string> = {
  偏多: "var(--up)",
  中性: "var(--text-muted)",
  偏空: "var(--down)",
};

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_0.8fr_0.9fr_1.3fr]";

type Quote = ReturnType<typeof useSymbolQuotes>["quotes"][number];

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
}: {
  results: readonly ScoreResult[];
  asOf: string | null;
  direction: LeaderboardDirection;
  onDirectionChange: (direction: LeaderboardDirection) => void;
  market: Market;
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
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
                    ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
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
        <LeaderboardRows results={previewResults} quoteBySymbol={previewQuoteBySymbol} t={t} />
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
        <LeaderboardRows results={results} quoteBySymbol={allQuoteBySymbol} t={t} />
      </ExpandModal>
    </section>
  );
}

/** The header row + data rows, shared verbatim between the card's inline preview and the full
 * list inside `ExpandModal` — one render path, so the two never drift in columns/formatting. */
function LeaderboardRows({
  results,
  quoteBySymbol,
  t,
}: {
  results: readonly ScoreResult[];
  quoteBySymbol: Map<string, Quote>;
  t: ReturnType<typeof useTranslations>;
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
        <span>{t("columnName")}</span>
        <span className="text-right">{t("columnPrice")}</span>
        <span className="text-right">{t("columnChange")}</span>
        <span className="text-center">{t("columnSparkline")}</span>
        <span className="text-center">{t("columnScore")}</span>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {results.map((result, index) => {
          const quote = quoteBySymbol.get(result.symbol);
          const changeUp = quote?.changePct !== null && (quote?.changePct ?? 0) >= 0;
          const changeColor = changeUp ? "var(--up)" : "var(--down)";
          return (
            <li
              key={result.symbol}
              role="group"
              aria-label={`${quote?.name ?? result.symbol} ${result.symbol} ${result.band} ${t(
                "scoreAria",
              )} ${
                result.composite !== null ? formatNumber(result.composite, "zh-TW") : t("empty")
              }`}
              className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
            >
              <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
                {index + 1}
              </span>
              <span aria-hidden className="truncate text-[14px] text-[var(--text)]">
                {quote?.name ?? result.symbol}
                <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">
                  {result.symbol}
                </span>
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[13px] text-[var(--text)] tabular-nums"
              >
                {quote?.price !== null && quote?.price !== undefined
                  ? formatNumber(quote.price, "zh-TW", { decimals: 1 })
                  : "—"}
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[11.5px] tabular-nums"
                style={{ color: quote?.changePct !== null ? changeColor : "var(--text-muted)" }}
              >
                {quote?.changePct !== null && quote?.changePct !== undefined
                  ? `${changeUp ? "▲" : "▼"}${formatNumber(Math.abs(quote.changePct), "zh-TW", { decimals: 2 })}%`
                  : "—"}
              </span>
              <span aria-hidden className="h-[26px]">
                <Sparkline values={quote?.sparkline ?? []} color={changeColor} />
              </span>
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
            </li>
          );
        })}
      </ul>
    </>
  );
}
