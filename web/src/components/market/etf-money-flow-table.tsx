"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import type { EtfMoneyFlowRow } from "@/types/market";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.9fr_1fr_0.8fr_0.9fr]";

/** A boolean "is this an upward move" flag's `--up`/`--down` color — pulled out of
 * `EtfMoneyFlowRowItem`'s own ternary chain purely to avoid a nested ternary. */
function signedColorForUp(up: boolean): string {
  return up ? "var(--up)" : "var(--down)";
}

/** ▲/▼ for a boolean "is this an upward move" flag — same purpose as `signedColorForUp`, split
 * out so the `${up ? "▲" : "▼"} ...` template-literal ternary never nests inside the outer
 * `changePct !== null ? ... : ...` check. */
function upDownArrow(up: boolean): string {
  return up ? "▲" : "▼";
}

/**
 * 資金持股 ranked list — one direction (inflow or outflow) at a time, picked by the caller via
 * `rows` (never both rendered together, mirroring the reference app's single active ranking).
 * Price/change% comes from `useSymbolQuotes` (the same batched `symbol-info`+`price-history`
 * hook `LeaderboardCard` already uses) since `EtfMoneyFlowRow` itself carries no price field —
 * this tool is a holdings/flow aggregation, not a quote source. The caller (`EtfMoneyFlowPanel`)
 * owns the section title/toggle/`<Caveat>` — this component is the table body only.
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
    rows.map((r) => r.component_stock_id),
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
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) => (
            <EtfMoneyFlowRowItem
              key={row.component_stock_id}
              row={row}
              quote={quoteBySymbol.get(row.component_stock_id)}
              locale={locale}
              t={t}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

function EtfMoneyFlowRowItem({
  row,
  quote,
  locale,
  t,
}: {
  readonly row: EtfMoneyFlowRow;
  readonly quote: ReturnType<typeof useSymbolQuotes>["quotes"][number] | undefined;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const changePct = quote?.changePct ?? null;
  const up = (changePct ?? 0) >= 0;
  const color = changePct === null ? "var(--text-muted)" : signedColorForUp(up);
  const hasHeldValue = row.held_market_value !== null && row.held_market_value !== undefined;
  const hasHeldShares = row.held_shares !== null && row.held_shares !== undefined;

  return (
    <li
      role="group"
      aria-label={`${quote?.name ?? row.component_stock_name} ${row.component_stock_id} ${
        changePct !== null
          ? `${upDownArrow(up)} ${formatNumber(Math.abs(changePct), locale, { decimals: 2 })}%`
          : t("noChangeData")
      }`}
      className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
    >
      <span aria-hidden className="truncate text-[13px] text-[var(--text)]">
        {quote?.name ?? row.component_stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
          {row.component_stock_id}
        </span>
      </span>
      <span aria-hidden className="text-right font-num text-[12px] tabular-nums" style={{ color }}>
        {changePct !== null
          ? `${upDownArrow(up)}${formatNumber(Math.abs(changePct), locale, { decimals: 2 })}%`
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
    </li>
  );
}
