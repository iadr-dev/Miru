"use client";

import { CaretLeftIcon, CaretRightIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useRef } from "react";
import { PublicStripItem } from "@/components/market/market-index-strip";
import { getUsMarketIndexStrip } from "@/lib/api/market";

const SCROLL_STEP_PX = 240;

/**
 * US-tab ticker strip — S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield/Fed funds rate, all public
 * (`get_us_market_index_strip`). Simpler than `MarketIndexStrip` (the TW strip): every item here
 * is `DataClass.PUBLIC` — no Sponsor-gated items to mask, so this reuses `PublicStripItem`'s
 * chip rendering directly with no `SponsorStripItem`/masked-teaser branching at all.
 */
export function UsMarketIndexStrip(): ReactNode {
  const t = useTranslations("market.cards.indexStrip");
  const scrollRef = useRef<HTMLDivElement>(null);

  const query = useQuery({
    queryKey: ["us_market_index_strip"],
    queryFn: () => getUsMarketIndexStrip(),
  });

  function scroll(direction: -1 | 1): void {
    scrollRef.current?.scrollBy({ left: direction * SCROLL_STEP_PX, behavior: "smooth" });
  }

  if (query.isPending) {
    return (
      <div className="flex h-16 w-full animate-pulse rounded-[var(--radius-card)] bg-[var(--surface-2)]" />
    );
  }

  return (
    <div className="relative flex items-center gap-1">
      <button
        type="button"
        onClick={() => scroll(-1)}
        aria-label={t("scrollLeft")}
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <CaretLeftIcon size={16} weight="regular" aria-hidden />
      </button>

      <div
        ref={scrollRef}
        className="flex flex-1 gap-2 overflow-x-auto scroll-smooth"
        style={{ scrollbarWidth: "none" }}
      >
        {query.data?.rows.map((item) => (
          <PublicStripItem key={item.key} item={item} />
        ))}
      </div>

      <button
        type="button"
        onClick={() => scroll(1)}
        aria-label={t("scrollRight")}
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <CaretRightIcon size={16} weight="regular" aria-hidden />
      </button>
    </div>
  );
}
