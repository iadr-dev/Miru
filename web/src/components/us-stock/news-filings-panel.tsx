"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { NewsSourceLogo } from "@/components/ui/news-source-logo";
import { QueryFailure } from "@/components/ui/query-state";
import { getNewsFeed, getSymbolInfo } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { getSecFilings } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { safeHttpUrl } from "@/lib/safe-url";
import type { NewsFeedItem, NewsFeedResponse } from "@/types/market/news";
import { isSourceDown } from "@/types/market/shared";
import type { Market } from "@/types/settings";

type NewsFilingsSubTab = "news" | "filings";
const NEWS_ROW_LIMIT = 20;

/** 新聞公告 tab — replaces the TW stock-detail page's 相關新聞 tab (FinMind news + PTT, both
 * structurally TW-only sources) with `get_news_feed` (a genuinely all-market RSS/Finnhub
 * aggregator, keyed by the company name as a free-text query — see that tool's own
 * `source_priority.yaml` comment: "keyless/near-keyless, all-market capable") plus a new SEC
 * EDGAR filings list (`get_sec_filings`, US-only — no other market this codebase serves has an
 * equivalent regulatory-filing feed). */
export function NewsFilingsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.newsFilings");
  const [subTab, setSubTab] = useState<NewsFilingsSubTab>("news");

  return (
    <div className="surface-card p-4 sm:p-5">
      <div role="tablist" aria-label={t("label")} className="mb-3 flex flex-wrap gap-1.5">
        {(["news", "filings"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={subTab === key}
            onClick={() => setSubTab(key)}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              subTab === key
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(key)}
          </button>
        ))}
      </div>

      {subTab === "news" ? (
        <NewsFeedList symbol={symbol} market={market} />
      ) : (
        <SecFilingsList symbol={symbol} market={market} />
      )}
    </div>
  );
}

function NewsFeedList({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const symbolInfoQuery = useQuery({
    queryKey: queryKeys.symbolRead("symbol_info", symbol, market),
    queryFn: () => getSymbolInfo([symbol], market),
  });
  const companyName = symbolInfoQuery.data?.rows[0]?.name ?? symbol;

  const newsQuery = useQuery({
    queryKey: queryKeys.read("news_feed", companyName),
    queryFn: () => getNewsFeed(companyName),
    enabled: Boolean(companyName),
  });

  if (symbolInfoQuery.isLoading || newsQuery.isLoading) return <CardSkeletonBody height={480} />;
  // The RSS/Finnhub aggregator can return the same story more than once (e.g. re-syndicated
  // across feeds) with an identical `link` — dedupe by link before slicing/rendering, the same
  // reasoning `stock-news-panel.tsx`'s own `NewsRowList` applies to its TW feed.
  const deduped = Array.from(
    new Map((newsQuery.data?.items ?? []).map((item) => [item.link, item])).values(),
  );
  return (
    <NewsRows
      items={deduped.slice(0, NEWS_ROW_LIMIT)}
      health={newsQuery.data?.health_status ?? (newsQuery.isError ? "unreachable" : "ok")}
    />
  );
}

/** Items present (always shown), source down (an outage is NOT "no related news"), or genuinely
 * nothing. */
export function NewsRows({
  items,
  health,
}: {
  readonly items: readonly NewsFeedItem[];
  readonly health: NewsFeedResponse["health_status"];
}): ReactNode {
  const t = useTranslations("usStock.newsFilings");
  if (items.length === 0) {
    if (isSourceDown(health)) {
      return <EmptyState compact title={t("newsUnavailable")} />;
    }
    return <p className="text-[13px] text-[var(--text-muted)]">{t("newsEmpty")}</p>;
  }

  return (
    <ul className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
      {items.map((item) => {
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
                <span className="font-num tabular-nums">{item.published_at.slice(0, 10)}</span>
              </span>
            </span>
          </>
        );
        return (
          <li key={item.link} className="border-[var(--border)] border-b py-2.5">
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
              <span className="grid min-h-11 w-full grid-cols-[20px_1fr] items-start gap-2">
                {body}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SecFilingsList({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.newsFilings");
  const query = useQuery({
    queryKey: queryKeys.symbolRead("sec_filings", symbol, market),
    queryFn: () => getSecFilings(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={480} />;
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const filings = query.data?.filings ?? [];
  if (filings.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("filingsEmpty")}</p>;
  }

  return (
    <ul className="flex flex-col gap-2.5">
      {filings.map((filing) => {
        const href = safeHttpUrl(filing.edgar_url ?? "");
        const body = (
          <>
            <span className="shrink-0 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]">
              {filing.filing_type}
            </span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
              {filing.title}
            </span>
            <span className="shrink-0 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
              {filing.filing_date}
            </span>
          </>
        );
        return (
          <li
            key={`${filing.filing_type}-${filing.filing_date}-${filing.title}`}
            className="flex items-center gap-2.5 border-[var(--border)] border-b py-2.5 last:border-b-0"
          >
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-h-11 w-full items-center gap-2.5 hover:underline"
              >
                {body}
              </a>
            ) : (
              <span className="flex min-h-11 w-full items-center gap-2.5">{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
