"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import {
  getDispositionBoard,
  getTagScreenerForSymbols,
  getTwMarketRankings,
} from "@/lib/api/market";
import type { DispositionBucket } from "@/types/market/disclosures";
import type { Market } from "@/types/settings";

// Fixed categorical `--chart-N` tokens, one per bucket — never signed (處置 status is a
// classification, not a price/%/score, per web/OVERVIEW.md §4).
const DISPOSITION_BUCKET_COLOR: Record<DispositionBucket, string> = {
  at_risk: "var(--chart-1)",
  in_disposition: "var(--chart-2)",
  exiting_soon: "var(--chart-3)",
};

// The 4 momentum/volume tag-catalog keys this badge row checks for, each its own fixed
// categorical token — never signed, same reasoning as the disposition bucket above. Hardcoded
// here (mirroring `EtfStructureBadges`' own 2-entry `SYMBOL_TYPE_BADGE` map) rather than fetching
// `get_tag_catalog` for a fixed 4-entry set that never changes at runtime.
const SIGNAL_TAG_BADGE: Record<string, { labelKey: string; color: string }> = {
  volume_surge: { labelKey: "volumeSurge", color: "var(--chart-4)" },
  volume_contraction: { labelKey: "volumeContraction", color: "var(--chart-5)" },
  new_52w_high: { labelKey: "new52wHigh", color: "var(--chart-6)" },
  new_52w_low: { labelKey: "new52wLow", color: "var(--chart-1)" },
};
const SIGNAL_TAGS = Object.keys(SIGNAL_TAG_BADGE);

/**
 * TW-stock-only signal badges next to the detail header's symbol name — surfaces facts already
 * computed elsewhere in the app (排行榜's 外資連買賣 streak, 處置 board bucket, momentum tag
 * screener) but never shown on the detail page itself. Three independent queries, each rendering
 * nothing while loading/absent — matches `EtfStructureBadges`'/`headerChipLabel`'s own "absent →
 * no chip" precedent in `asset-header.tsx`, never a skeleton placeholder chip.
 *
 * 外資連買/賣 is the one deliberate exception to this app's "紅漲綠跌 only for price/%/score"
 * rule (`web/OVERVIEW.md` §4) — confirmed with the user: a TW reader reads 連買/連賣 direction the
 * same way they read price direction, so this badge alone uses `--up`/`--down`. Every other
 * badge here (處置/爆量/量縮/創新高/創新低) stays strictly categorical.
 */
export function TwStockSignalBadges({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.assetHeader");
  const tDisposition = useTranslations("market.cards.dispositionBoard.bucket");
  const isTw = market === "TW";

  const streakQuery = useQuery({
    queryKey: ["tw_market_rankings", "foreign_streak", symbol],
    queryFn: () => getTwMarketRankings("foreign_streak", 50, symbol),
    enabled: isTw,
  });
  const streakRow = streakQuery.data?.rows[0];

  // Same query key `asset-header.tsx`'s own ETF path already establishes for `get_disposition_
  // board` is NOT reused here (that one is ETF-only via `getEtfList`) — this is the market page's
  // 處置 tab's own query key, so TanStack dedupes against whatever that tab has already fetched.
  const dispositionQuery = useQuery({
    queryKey: ["disposition_board", market],
    queryFn: () => getDispositionBoard(market),
    enabled: isTw,
  });
  const dispositionRow = dispositionQuery.data?.cards.find((card) => card.symbol === symbol);

  const tagQuery = useQuery({
    queryKey: ["tag_screener", market, symbol, SIGNAL_TAGS],
    queryFn: () => getTagScreenerForSymbols(SIGNAL_TAGS, [symbol], market),
    enabled: isTw,
  });
  const matchedTags = tagQuery.data?.result.hits[0]?.matched_tags ?? [];

  if (!isTw) return null;

  return (
    <>
      {streakRow && streakRow.extra_value !== 0 ? (
        <Badge
          label={streakRow.extra_label}
          color={streakRow.extra_value > 0 ? "var(--up)" : "var(--down)"}
        />
      ) : null}
      {dispositionRow && !dispositionRow.locked ? (
        <Badge
          label={tDisposition(dispositionRow.bucket)}
          color={DISPOSITION_BUCKET_COLOR[dispositionRow.bucket]}
        />
      ) : null}
      {matchedTags.map((tag) => {
        const badge = SIGNAL_TAG_BADGE[tag];
        return badge ? <Badge key={tag} label={t(badge.labelKey)} color={badge.color} /> : null;
      })}
    </>
  );
}
