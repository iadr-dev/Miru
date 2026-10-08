"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { LockedRowsNote } from "@/components/market/locked-rows-note";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { IndustryChainMoneyFlowRow } from "@/types/market";

const MAX_BAR_PCT = 40; // clamps the bar-length scale so one dominant chain doesn't flatten the rest
// Same collapsed-row-count convention every other market card in this dashboard uses.
const INDUSTRY_MONEY_FLOW_COLLAPSED_ROW_COUNT = 10;

/**
 * 產業鏈資金流向 card — title + ranked list. One row per industry-chain TOTAL (`is_total`;
 * excludes sub-industry rows, which don't sum to the chain total — see
 * `IndustryChainMoneyFlowRow`'s own docstring), pre-ranked/pre-masked server-side by
 * `trading_money_pct` descending (governance.md §4 top-N-preview exception). Bar fill uses
 * `--chart-1` rather than `--up`/`--down`: `trading_money_pct` is a non-signed
 * share-of-market-turnover figure, not a price/% change, so the signed 紅漲綠跌 tokens don't
 * apply — this is exactly the "categorical, never signed" case `--chart-1..6` exists for.
 * Collapses to the top 10 inline, same "展開完整 N 筆" `ExpandModal` pattern every other
 * ranked-list market card in this dashboard follows.
 */
export function IndustryMoneyFlowCard({
  rows,
}: {
  readonly rows: readonly IndustryChainMoneyFlowRow[];
}): ReactNode {
  const t = useTranslations("market.cards.industryFlow");
  const [expanded, setExpanded] = useState(false);
  const totals = [...rows].filter((row) => row.is_total).sort((a, b) => a.rank - b.rank);

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      {totals.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <>
          <IndustryMoneyFlowRows rows={totals.slice(0, INDUSTRY_MONEY_FLOW_COLLAPSED_ROW_COUNT)} />
          {totals.length > INDUSTRY_MONEY_FLOW_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className={cn(
                "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              )}
            >
              <span>{t("expandTo", { count: totals.length })}</span>
            </button>
          ) : null}
          <ExpandModal
            open={expanded}
            title={t("title")}
            closeLabel={t("collapse")}
            onClose={() => setExpanded(false)}
          >
            <IndustryMoneyFlowRows rows={totals} />
          </ExpandModal>
        </>
      )}
    </section>
  );
}

/** The row list itself, shared verbatim between the card's inline (sliced) preview and the
 * `ExpandModal` full list. */
function IndustryMoneyFlowRows({
  rows,
}: {
  readonly rows: readonly IndustryChainMoneyFlowRow[];
}): ReactNode {
  const t = useTranslations("market.cards.industryFlow");
  const locale = useLocale();

  return (
    <>
      <ul className="flex flex-col gap-2.5">
        {rows.map((row) =>
          row.locked ? (
            <LockedIndustryMoneyFlowRow key={row.rank} rank={row.rank} />
          ) : (
            <IndustryMoneyFlowRowItem key={row.industry} row={row} locale={locale} t={t} />
          ),
        )}
      </ul>
      <LockedRowsNote rows={rows} />
    </>
  );
}

/** governance.md §4 top-N-preview exception's row-level lock: every field but `rank`/`is_total`
 * is nulled server-side for a `locked` row — mirrors `daily-signal-table.tsx`'s own locked-row
 * pattern for this card's bar-row shape. */
function LockedIndustryMoneyFlowRow({ rank }: { readonly rank: number }): ReactNode {
  return (
    <li>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
          <LockIcon size={11} weight="regular" aria-hidden />
          <span aria-hidden>••••</span>
          <span className="font-num text-[11px] tabular-nums">#{rank}</span>
        </span>
        <span aria-hidden className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
          ••••
        </span>
      </div>
      <div
        aria-hidden
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]"
      />
      <p aria-hidden className="mt-1 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        ••••
      </p>
    </li>
  );
}

/** One real (never-locked) row — split out of `IndustryMoneyFlowRows` so that component's own
 * `.map` stays a simple locked/unlocked branch. */
function IndustryMoneyFlowRowItem({
  row,
  locale,
  t,
}: {
  readonly row: IndustryChainMoneyFlowRow;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const pct = row.trading_money_pct ?? 0;
  const barWidth = Math.min((pct / MAX_BAR_PCT) * 100, 100);
  return (
    <li aria-label={`${row.industry} ${formatNumber(pct, locale, { decimals: 1 })}%`}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="truncate text-[13px] text-[var(--text)]">{row.industry}</span>
        <span className="font-num text-[12px] text-[var(--text)] tabular-nums">
          {formatNumber(pct, locale, { decimals: 1 })}%
        </span>
      </div>
      <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div
          className={cn("h-full rounded-full bg-[var(--chart-1)]")}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      <p aria-hidden className="mt-1 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        {t("stockCount", { count: row.stock_count ?? 0 })} ·{" "}
        {formatCompactNumber(row.trading_money ?? 0, locale)}
      </p>
    </li>
  );
}
