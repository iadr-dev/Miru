"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LockedRowsNote } from "@/components/market/locked-rows-note";
import { SymbolLink } from "@/components/market/symbol-link";
import { WatchlistStarButton } from "@/components/stock/watchlist-star-button";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionColor, formatSignedChange } from "@/lib/signed";
import type { EtfMoneyFlowRow } from "@/types/market/flows";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.9fr_1fr_0.8fr_0.9fr_28px]";

/**
 * 資金持股 ranked list — one direction (inflow or outflow) at a time, picked by the caller via
 * `rows` (never both rendered together, mirroring the reference app's single active ranking).
 * Price/change% comes from `useSymbolQuotes` (the same batched `symbol-info`+`price-history`
 * hook `LeaderboardCard` already uses) since `EtfMoneyFlowRow` itself carries no price field —
 * this tool is a holdings/flow aggregation, not a quote source. The caller (`EtfMoneyFlowPanel`)
 * owns the section title/toggle — this component is the table body only.
 */
export function EtfMoneyFlowTable({
  rows,
  market,
}: {
  readonly rows: readonly EtfMoneyFlowRow[];
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.etfMoneyFlow");
  const locale = useLocale();
  const { quotes } = useSymbolQuotes(
    rows.map((r) => r.component_stock_id).filter((id): id is string => id !== null),
    market,
  );
  const quoteBySymbol = new Map(quotes.map((q) => [q.code, q]));

  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[640px]">
        <div
          aria-hidden
          className={cn(
            "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
            ROW_GRID_COLS,
          )}
        >
          <span>{t("columnStock")}</span>
          <span className="text-right">{t("columnChange")}</span>
          <span className="text-right">{t("columnHeldValue")}</span>
          <span className="text-right">{t("columnHolderCount")}</span>
          <span className="text-right">{t("columnSharesHeldPct")}</span>
          <span aria-hidden />
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) =>
            row.locked ? (
              <LockedEtfMoneyFlowRowItem key={row.rank} rank={row.rank} />
            ) : (
              <EtfMoneyFlowRowItem
                key={row.component_stock_id}
                row={row}
                quote={
                  row.component_stock_id ? quoteBySymbol.get(row.component_stock_id) : undefined
                }
                market={market}
                locale={locale}
                t={t}
              />
            ),
          )}
        </ul>
        <LockedRowsNote rows={rows} />
      </div>
    </div>
  );
}

/** governance.md §4 top-N-preview exception's row-level lock: every field but `rank` is nulled
 * server-side for a `locked` row — mirrors `daily-signal-table.tsx`'s own locked-row pattern for
 * this table's 5-column grid. */
function LockedEtfMoneyFlowRowItem({ rank }: { readonly rank: number }): ReactNode {
  return (
    <li className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}>
      <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
        <LockIcon size={12} weight="regular" aria-hidden />
        <span aria-hidden>••••</span>
        <span className="font-num text-[11px] tabular-nums">#{rank}</span>
      </span>
      <span aria-hidden className="text-right text-[12px] text-[var(--text-muted)]">
        ••••
      </span>
      <span
        aria-hidden
        className="flex flex-col items-end text-right text-[12px] text-[var(--text-muted)]"
      >
        ••••
      </span>
      <span aria-hidden className="text-right text-[12px] text-[var(--text-muted)]">
        ••••
      </span>
      <span aria-hidden className="text-right text-[12px] text-[var(--text-muted)]">
        ••••
      </span>
      <span aria-hidden />
    </li>
  );
}

function EtfMoneyFlowRowItem({
  row,
  quote,
  market,
  locale,
  t,
}: {
  readonly row: EtfMoneyFlowRow;
  readonly quote: ReturnType<typeof useSymbolQuotes>["quotes"][number] | undefined;
  readonly market: Market;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const changePct = quote?.changePct ?? null;
  const color = changePct === null ? "var(--text-muted)" : directionColor(changePct, 2);
  const hasHeldValue = row.held_market_value !== null && row.held_market_value !== undefined;
  const hasHeldShares = row.held_shares !== null && row.held_shares !== undefined;

  return (
    <li
      role="group"
      aria-label={`${quote?.name ?? row.component_stock_name} ${row.component_stock_id} ${
        changePct !== null
          ? formatSignedChange(changePct, locale, { decimals: 2, unit: "%" })
          : t("noChangeData")
      }`}
      className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
    >
      <SymbolLink
        symbol={row.component_stock_id ?? ""}
        market={market}
        className="truncate text-[13px] text-[var(--text)] hover:underline"
      >
        {quote?.name ?? row.component_stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
          {row.component_stock_id}
        </span>
      </SymbolLink>
      <span aria-hidden className="text-right font-num text-[12px] tabular-nums" style={{ color }}>
        {changePct !== null
          ? formatSignedChange(changePct, locale, { decimals: 2, unit: "%", spaced: false })
          : "—"}
      </span>
      <span aria-hidden className="flex flex-col items-end text-right">
        <span className="font-num text-[12px] text-[var(--text)] tabular-nums">
          {hasHeldValue ? formatCompactNumber(row.held_market_value ?? null, locale) : "—"}
        </span>
        {hasHeldShares ? (
          <span className="font-num text-[10px] text-[var(--text-muted)] tabular-nums">
            {formatNumber(row.held_shares ?? null, locale, { decimals: 0 })}
            {t("lots")}
          </span>
        ) : null}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.active_etf_holder_count ?? "—"}
      </span>
      <span aria-hidden className="text-right font-num text-[12px] text-[var(--text)] tabular-nums">
        {row.stock_shares_held_pct !== null && row.stock_shares_held_pct !== undefined
          ? `${formatNumber(row.stock_shares_held_pct, locale, { decimals: 2 })}%`
          : "—"}
      </span>
      {row.component_stock_id ? (
        <WatchlistStarButton
          symbol={row.component_stock_id}
          market={market}
          assetClass="stock"
          size="compact"
        />
      ) : (
        <span aria-hidden />
      )}
    </li>
  );
}
