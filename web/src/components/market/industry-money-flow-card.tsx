"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import type { IndustryChainMoneyFlowRow } from "@/types/market";

const MAX_BAR_PCT = 40; // clamps the bar-length scale so one dominant chain doesn't flatten the rest

/**
 * 產業鏈資金流向 ranked list — one row per industry-chain TOTAL (`sub_industry === ""`; excludes
 * sub-industry rows, which don't sum to the chain total — see `IndustryChainMoneyFlowRow`'s own
 * docstring), ranked by `trading_money_pct` descending. Bar fill uses `--chart-1` rather than
 * `--up`/`--down`: `trading_money_pct` is a non-signed share-of-market-turnover figure, not a
 * price/% change, so the signed 紅漲綠跌 tokens don't apply — this is exactly the "categorical,
 * never signed" case `--chart-1..6` exists for.
 */
export function IndustryMoneyFlowCard({
  rows,
}: {
  readonly rows: readonly IndustryChainMoneyFlowRow[];
}): ReactNode {
  const t = useTranslations("market.cards.industryFlow");
  const locale = useLocale();
  const totals = [...rows]
    .filter((row) => row.sub_industry === "")
    .sort((a, b) => b.trading_money_pct - a.trading_money_pct);

  if (totals.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <ul className="flex flex-col gap-2.5">
      {totals.map((row) => {
        const barWidth = Math.min((row.trading_money_pct / MAX_BAR_PCT) * 100, 100);
        return (
          <li
            key={row.industry}
            aria-label={`${row.industry} ${formatNumber(row.trading_money_pct, locale, { decimals: 1 })}%`}
          >
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <span className="truncate text-[13px] text-[var(--text)]">{row.industry}</span>
              <span className="font-num text-[12px] text-[var(--text)] tabular-nums">
                {formatNumber(row.trading_money_pct, locale, { decimals: 1 })}%
              </span>
            </div>
            <div
              aria-hidden
              className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]"
            >
              <div
                className={cn("h-full rounded-full bg-[var(--chart-1)]")}
                style={{ width: `${barWidth}%` }}
              />
            </div>
            <p
              aria-hidden
              className="mt-1 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums"
            >
              {t("stockCount", { count: row.stock_count })} ·{" "}
              {formatCompactNumber(row.trading_money, locale)}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
