"use client";

import { GlobeIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryState } from "@/components/ui/query-state";
import { getNewsFeed } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { cn } from "@/lib/cn";
import { formatNewsTime } from "@/lib/format-news-time";
import { safeHttpUrl } from "@/lib/safe-url";
import { sourceLogoUrl } from "@/lib/source-logo";
import type { NewsFeedItem, NewsFeedResponse } from "@/types/market/news";
import { isSourceDown } from "@/types/market/shared";
import type { Market } from "@/types/settings";

const NEWS_QUERY_BY_MARKET: Record<Market, string> = {
  TW: "台股",
  US: "US stock market",
};

// This sub-tab has no sibling card next to it (unlike 每日焦點's multi-card grid), so its own
// list needs to fill the tab's width/height on its own rather than staying a single narrow
// column — 40 items across a 2-column grid, vs.
// `MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT`'s 5 (that card always sits beside 3 others in
// a grid cell, so a narrow single column is correct there).
const NEWS_FEED_COLLAPSED_ROW_COUNT = 40;

/**
 * 即時新聞 — the TW/US tab's News sub-tab. Public (`DataClass.PUBLIC`) — no `SponsorTeaser`/
 * masked-teaser branch, mirroring `MaterialAnnouncementFeedPanel`'s own explicit no-blur
 * requirement. Backed by `get_news_feed` (`sources/news.py`'s RSS/Finnhub aggregator) — distinct
 * from FinMind's own per-symbol `get_finmind_stock_news` dataset, never merged with it here.
 *
 * `market` now genuinely branches the backend's own source mix (`get_news_feed`'s own docstring):
 * `market=TW` fetches the fixed TW financial RSS feeds plus Finnhub company-news scoped by
 * `query`; any other market skips RSS entirely (those feeds are TW-only outlets) and uses
 * Finnhub's general market-news category instead — fixing a real bug where the US panel's
 * Finnhub call passed a free-text phrase as a company-news `symbol` param (which Finnhub
 * requires to be a real ticker) and silently returned zero items, leaving only TW-mixed RSS
 * content visible.
 */
export function NewsFeedPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.newsFeed");
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: queryKeys.marketRead("news_feed", market),
    queryFn: () => getNewsFeed(NEWS_QUERY_BY_MARKET[market], market),
  });

  return (
    <section className="surface-card flex min-h-[70vh] w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={600} />}>
        {(data) => (
          <NewsFeedContent data={data} expanded={expanded} onExpandedChange={setExpanded} />
        )}
      </QueryState>
    </section>
  );
}

/** The three states of the feed: items present (always shown, even when a refresh partly
 * failed), source down (an outage is NOT "no news" - an empty list from an unreachable source
 * says so), or genuinely no items. */
export function NewsFeedContent({
  data,
  expanded,
  onExpandedChange,
}: {
  readonly data: NewsFeedResponse;
  readonly expanded: boolean;
  readonly onExpandedChange: (next: boolean) => void;
}): ReactNode {
  const t = useTranslations("market.cards.newsFeed");

  if (data.items.length === 0) {
    if (isSourceDown(data.health_status)) {
      return <EmptyState compact title={t("unavailable")} />;
    }
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <>
      <NewsFeedList items={data.items.slice(0, NEWS_FEED_COLLAPSED_ROW_COUNT)} />

      {data.items.length > NEWS_FEED_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => onExpandedChange(true)}
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
        onClose={() => onExpandedChange(false)}
      >
        <NewsFeedList items={data.items} />
      </ExpandModal>
    </>
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
  const locale = useLocale();
  const when = formatNewsTime(item.published_at, locale);
  // `item.link` is third-party RSS/Finnhub content, never trusted as-is (web/OVERVIEW.md §8) —
  // reject a `javascript:`/`data:` URI before it ever reaches an `href`.
  const href = safeHttpUrl(item.link);
  const body = (
    <>
      <NewsSourceLogo url={item.link} />
      <span className="min-w-0">
        <span className="line-clamp-2 text-[13px] text-[var(--text)] hover:underline">
          {item.title}
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
          {item.source}
          <time dateTime={item.published_at} title={when.full} className="font-num tabular-nums">
            {when.label}
          </time>
        </span>
      </span>
    </>
  );

  return (
    <li className="border-[var(--border)] border-b py-2.5">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="grid min-h-11 w-full grid-cols-[20px_1fr] items-start gap-2"
        >
          {body}
        </a>
      ) : (
        <span className="grid min-h-11 w-full grid-cols-[20px_1fr] items-start gap-2">{body}</span>
      )}
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
