"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { SymbolLink } from "@/components/market/symbol-link";
import { cn } from "@/lib/cn";
import type { MarketRankingRow } from "@/types/market";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[24px_1.3fr_0.8fr_0.8fr_0.8fr_0.9fr]";

/** The 5 non-漲跌幅 排行榜 tabs' shared row list (熱門美股/市值/營收/ROE/成交量) — every field
 * comes straight from `get_us_market_rankings`' own row (a live Yahoo screener quote), never
 * `useSymbolQuotes`' enrichment (`get_symbol_info`/`get_price_history`): a screener quote already
 * carries name/price/change/volume in one call, so there is nothing left to enrich. No sparkline
 * column either — the screener returns one point-in-time quote, not a price history window. */
export function UsMarketRankingRows({
  rows,
  market,
}: {
  readonly rows: readonly MarketRankingRow[];
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.rankings");

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
        <span>{t("columnName")}</span>
        <span className="text-right">{t("columnPrice")}</span>
        <span className="text-right">{t("columnChangePct")}</span>
        <span className="text-right">{t("columnChange")}</span>
        <span className="text-right">{t("columnVolume")}</span>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {rows.map((row, index) => {
          const changePct = row.change_pct;
          const up = changePct !== null && changePct >= 0;
          const color =
            changePct !== null ? (up ? "var(--up)" : "var(--down)") : "var(--text-muted)";
          const name = row.name ?? row.symbol;
          const change = row.change;
          return (
            <li
              key={row.symbol}
              role="group"
              aria-label={`${name} ${row.symbol} ${
                changePct !== null
                  ? `${up ? "▲" : "▼"} ${formatNumber(Math.abs(changePct), "zh-TW", { decimals: 2 })}%`
                  : t("empty")
              }`}
              className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
            >
              <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
                {index + 1}
              </span>
              <SymbolLink
                symbol={row.symbol}
                market={market}
                className="truncate text-[14px] text-[var(--text)] hover:underline"
              >
                {name}
                <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">{row.symbol}</span>
              </SymbolLink>
              <span
                aria-hidden
                className="text-right font-num text-[13px] text-[var(--text)] tabular-nums"
              >
                {row.price !== null ? formatNumber(row.price, "zh-TW", { decimals: 2 }) : "—"}
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12.5px] tabular-nums"
                style={{ color }}
              >
                {changePct !== null
                  ? `${up ? "▲" : "▼"}${formatNumber(Math.abs(changePct), "zh-TW", { decimals: 2 })}%`
                  : "—"}
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12.5px] tabular-nums"
                style={{ color }}
              >
                {change !== null
                  ? `${change >= 0 ? "+" : ""}${formatNumber(change, "zh-TW", { decimals: 2 })}`
                  : "—"}
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12.5px] text-[var(--text-muted)] tabular-nums"
              >
                {row.volume !== null ? formatNumber(row.volume, "zh-TW", { decimals: 0 }) : "—"}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
