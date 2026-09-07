"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { DispositionStockCard } from "@/components/market/disposition-stock-card";
import { SponsorGatedBody } from "@/components/market/sponsor-teaser";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { getDispositionBoard } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import {
  DISPOSITION_BUCKETS,
  type DispositionBoardCardRow,
  type DispositionBucket,
} from "@/types/market";
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
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={420} />}>
        {(data) => (
          <SponsorGatedBody data={data} title={t("title")} unavailableCopy={t("unavailable")}>
            {(data) => (
              <DispositionBoardBody
                bucket={bucket}
                onBucketChange={setBucket}
                search={search}
                onSearchChange={setSearch}
                bucketCounts={data.bucket_counts}
                cards={data.cards}
                market={market}
              />
            )}
          </SponsorGatedBody>
        )}
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
      if (!query) return true;
      return (
        card.symbol.toLowerCase().includes(query) || card.company_name.toLowerCase().includes(query)
      );
    });
  }, [cards, bucket, query]);
  const { quotes } = useSymbolQuotes(
    filteredCards.map((card) => card.symbol),
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
                ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
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
              key={card.symbol}
              card={card}
              sparkline={quoteBySymbol.get(card.symbol)?.sparkline ?? []}
            />
          ))}
        </div>
      )}
    </>
  );
}
