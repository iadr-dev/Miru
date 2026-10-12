"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { DispositionStockCard } from "@/components/market/disposition-stock-card";
import { QueryState } from "@/components/ui/query-state";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { getDispositionBoard } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import {
  DISPOSITION_BUCKETS,
  type DispositionBoardCardRow,
  type DispositionBucket,
} from "@/types/market/disclosures";
import type { Market } from "@/types/settings";

/**
 * 處置王-style disposition board — the TW tab's 處置 sub-tab. Sponsor-tier
 * (`DataClass.SPONSOR`), gated via `SponsorGatedBody` like every other Sponsor card in this
 * dashboard. 3 bucket tabs (風險股/處置中/即將出關) with live counts + a symbol/name search box
 * over a responsive card grid. 自結發布 is out of scope — no confirmed backend data source
 * exists for it yet (see the backend plan this mirrors).
 */
export function DispositionBoardPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.dispositionBoard");
  const [bucket, setBucket] = useState<DispositionBucket>("in_disposition");
  const [search, setSearch] = useState("");

  const query = useQuery({
    queryKey: ["disposition_board", market],
    queryFn: () => getDispositionBoard(market),
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={420} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <>
                <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
                <p className="mt-3 text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("unavailable")}
                </p>
              </>
            );
          }
          return (
            <DispositionBoardBody
              bucket={bucket}
              onBucketChange={setBucket}
              search={search}
              onSearchChange={setSearch}
              bucketCounts={data.bucket_counts}
              cards={data.cards}
              market={market}
            />
          );
        }}
      </QueryState>
    </section>
  );
}

function DispositionBoardBody({
  bucket,
  onBucketChange,
  search,
  onSearchChange,
  bucketCounts,
  cards,
  market,
}: {
  readonly bucket: DispositionBucket;
  readonly onBucketChange: (bucket: DispositionBucket) => void;
  readonly search: string;
  readonly onSearchChange: (value: string) => void;
  readonly bucketCounts: Record<string, number>;
  readonly cards: readonly DispositionBoardCardRow[];
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.dispositionBoard");
  const query = search.trim().toLowerCase();
  const filteredCards = useMemo(() => {
    return cards.filter((card) => {
      if (card.bucket !== bucket) return false;
      if (card.locked) return true;
      if (!query) return true;
      return (
        (card.symbol?.toLowerCase().includes(query) ?? false) ||
        (card.company_name?.toLowerCase().includes(query) ?? false)
      );
    });
  }, [cards, bucket, query]);
  const { quotes } = useSymbolQuotes(
    filteredCards.map((card) => card.symbol).filter((s): s is string => s !== null),
    market,
  );
  const quoteBySymbol = new Map(quotes.map((q) => [q.code, q]));

  return (
    <>
      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-1.5">
        {DISPOSITION_BUCKETS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={bucket === key}
            onClick={() => onBucketChange(key)}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              bucket === key
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`bucket.${key}`)} {bucketCounts[key] ?? 0}
          </button>
        ))}
      </div>

      <input
        type="search"
        name="disposition-board-search"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder={t("search")}
        aria-label={t("search")}
        className={cn(
          "mt-3 w-full max-w-xs rounded-[var(--radius-input)] border border-[var(--border)]",
          "bg-[var(--surface-2)] px-3 py-2 text-[13px] text-[var(--text)] placeholder:text-[var(--text-muted)]",
          "focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
        )}
      />

      {filteredCards.length === 0 ? (
        <p className="mt-4 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {filteredCards.map((card) => (
            <DispositionStockCard
              key={card.symbol ?? `locked-${card.bucket}-${card.rank}`}
              card={card}
              sparkline={(card.symbol && quoteBySymbol.get(card.symbol)?.sparkline) || []}
            />
          ))}
        </div>
      )}
    </>
  );
}
