"use client";

import { GlobeIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getNewsFeed } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { sourceLogoUrl } from "@/lib/source-logo";
import type { NewsFeedItem } from "@/types/market";

// This sub-tab has no sibling card next to it (unlike 每日焦點's multi-card grid), so its own
// list needs to fill the tab's width/height on its own rather than staying a single narrow
// column — 40 items across a 2-column grid, vs.
// `MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT`'s 5 (that card always sits beside 3 others in
// a grid cell, so a narrow single column is correct there).
const NEWS_FEED_COLLAPSED_ROW_COUNT = 40;

/**
 * 即時新聞 — the TW tab's News sub-tab. Public (`DataClass.PUBLIC`) — no `SponsorTeaser`/
 * masked-teaser branch, mirroring `MaterialAnnouncementFeedPanel`'s own explicit no-blur
 * requirement. Backed by `get_news_feed` (`sources/news.py`'s RSS/Finnhub aggregator) — distinct
 * from FinMind's own per-symbol `get_finmind_stock_news` dataset, never merged with it here.
 */
export function NewsFeedPanel(): ReactNode {
  const t = useTranslations("market.cards.newsFeed");
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: ["news_feed"],
    queryFn: () => getNewsFeed(),
  });

  return (
    <section className="flex min-h-[70vh] w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={600} />}>
        {(data) => {
          if (data.items.length === 0) {
            return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
          }

          return (
            <>
              <NewsFeedList items={data.items.slice(0, NEWS_FEED_COLLAPSED_ROW_COUNT)} />

              {data.items.length > NEWS_FEED_COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: data.items.length })}</span>
                </button>
              ) : null}

              <ExpandModal
                open={expanded}
                title={t("title")}
                closeLabel={t("collapse")}
                onClose={() => setExpanded(false)}
              >
                <NewsFeedList items={data.items} />
              </ExpandModal>
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

function NewsFeedList({ items }: { readonly items: readonly NewsFeedItem[] }): ReactNode {
  // The RSS aggregator can surface the same story twice under an identical `link` (a feed
  // re-publishing its own item, or two feeds syndicating the same wire story) — dedupe here
  // rather than keying by array index, which would break React's identity tracking on refetch.
  const deduped = Array.from(new Map(items.map((item) => [item.link, item])).values());

  return (
    <ul className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
      {deduped.map((item) => (
        <NewsFeedRow key={item.link} item={item} />
      ))}
    </ul>
  );
}

function NewsFeedRow({ item }: { readonly item: NewsFeedItem }): ReactNode {
  return (
    <li className="border-b border-[var(--border)] py-2.5">
      <a
        href={item.link}
        target="_blank"
        rel="noopener noreferrer"
        className="grid min-h-11 w-full grid-cols-[20px_1fr] items-start gap-2"
      >
        <NewsSourceLogo url={item.link} />
        <span className="min-w-0">
          <span className="line-clamp-2 text-[13px] text-[var(--text)] hover:underline">
            {item.title}
          </span>
          <span className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
            {item.source}
            <span className="font-num tabular-nums">{item.published_at}</span>
          </span>
        </span>
      </a>
    </li>
  );
}

/** Same "pre-downloaded local logo, favicon-service fallback, globe glyph on load failure"
 * pattern `web-search-sources.tsx`'s own `SourceLogo` establishes — mirrored rather than shared,
 * since that one lives under `app/[locale]/(app)/chat/` and this is a `components/market/` leaf. */
function NewsSourceLogo({ url }: { readonly url: string }): ReactNode {
  const logoUrl = sourceLogoUrl(url);
  const [failed, setFailed] = useState(false);

  if (!logoUrl || failed) {
    return (
      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center text-[var(--text-muted)]">
        <GlobeIcon size={14} weight="regular" aria-hidden />
      </span>
    );
  }

  return (
    // biome-ignore lint/performance/noImgElement: dynamic per-source domain, remotePatterns can't allowlist it
    <img
      src={logoUrl}
      alt=""
      width={20}
      height={20}
      className="mt-0.5 size-5 shrink-0 rounded-[4px] object-contain"
      onError={() => setFailed(true)}
    />
  );
}
