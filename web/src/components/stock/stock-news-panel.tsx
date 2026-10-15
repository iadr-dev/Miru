"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ExpandModal } from "@/components/ui/expand-modal";
import { NewsSourceLogo } from "@/components/ui/news-source-logo";
import { QueryFailure } from "@/components/ui/query-state";
import { getSymbolInfo } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { getFinmindStockNews, getPttStockSearch } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { safeHttpUrl } from "@/lib/safe-url";
import type { Market } from "@/types/settings";
import type { PttStockSearchPost, StockNewsRow } from "@/types/stock/news";

const NEWS_COLLAPSED_ROW_COUNT = 20;
const PTT_COLLAPSED_ROW_COUNT = 20;
const NEWS_LOOKBACK_DAYS = 7;

// `get_finmind_stock_news` is single-trading-day-per-request upstream (confirmed empirically —
// FinMind 400s if `end_date` is present; `start_date` alone returns exactly that one day, never
// a range) — a caller who wants a week of coverage must fire one request per day and merge, not
// pass a wider start/end window and expect more rows back.
function lastNDaysIso(days: number): string[] {
  const dates: string[] = [];
  for (let i = 0; i < days; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    dates.push(d.toISOString().slice(0, 10));
  }
  return dates;
}

const PTT_LOOKBACK_DAYS = 7;

type NewsSubTab = "news" | "ptt";
type PttSort = "latest" | "hot";

/**
 * 相關新聞 tab — 2 sub-tabs, not the reference app's 3: 新聞 (`get_finmind_stock_news`, real
 * per-symbol FinMind dataset, `DataClass.PUBLIC`, chronological only — no popularity signal
 * exists in that dataset, so no 最新/熱門 toggle here) and PTT (`get_ptt_stock_search`,
 * best-effort match against the symbol/company name in each candidate post's BODY text — not
 * just the title, since most real posts about a stock never name it in the title — across
 * Stock/Tech_Job/Warrant boards, WITH a real 最新/熱門 toggle since `push_count` is a genuine
 * signal on that dataset). 討論 is dropped entirely — no discussion-board capability exists
 * anywhere in this backend, and this codebase never ships a placeholder tab with fabricated
 * content.
 */
export function StockNewsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.news");
  const [subTab, setSubTab] = useState<NewsSubTab>("news");

  return (
    <div className="surface-card p-4 sm:p-5">
      <div role="tablist" aria-label={t("label")} className="mb-3 flex flex-wrap gap-1.5">
        {(["news", "ptt"] as const).map((key) => (
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
        <StockNewsList symbol={symbol} market={market} />
      ) : (
        <StockPttList symbol={symbol} market={market} />
      )}
    </div>
  );
}

function StockNewsList({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.news");
  const [expanded, setExpanded] = useState(false);
  const days = useMemo(() => lastNDaysIso(NEWS_LOOKBACK_DAYS), []);
  const query = useQuery({
    queryKey: queryKeys.symbolRead("stock_finmind_news", symbol, market, days),
    queryFn: async () => {
      const perDay = await Promise.all(
        days.map((day) => getFinmindStockNews(symbol, market, { startDate: day })),
      );
      return perDay.flatMap((r) => r.rows);
    },
  });

  if (query.isLoading) return <CardSkeletonBody height={480} />;
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const rows = query.data ?? [];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("newsEmpty")}</p>;
  }
  const sorted = [...rows].sort((a, b) => b.trade_date.localeCompare(a.trade_date));

  return (
    <>
      <NewsRowList rows={sorted.slice(0, NEWS_COLLAPSED_ROW_COUNT)} />
      {sorted.length > NEWS_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: sorted.length })}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t("news")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <NewsRowList rows={sorted} />
      </ExpandModal>
    </>
  );
}

function NewsRowList({ rows }: { readonly rows: readonly StockNewsRow[] }): ReactNode {
  // FinMind's per-symbol news dump can carry the same story under two source labels (confirmed
  // live for 2330: identical `link`, "CMoney" vs "cmoney.tw") — dedupe by link, same reasoning
  // `NewsFeedList`/`PttPostList` already apply to their own whole-market feeds.
  const deduped = Array.from(new Map(rows.map((row) => [row.link, row])).values());

  return (
    <ul className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
      {deduped.map((row) => (
        <NewsRow key={row.link} row={row} />
      ))}
    </ul>
  );
}

function NewsRow({ row }: { readonly row: StockNewsRow }): ReactNode {
  const href = safeHttpUrl(row.link);
  const body = (
    <>
      <NewsSourceLogo url={row.link} />
      <span className="min-w-0">
        <span className="line-clamp-2 text-[13px] text-[var(--text)] hover:underline">
          {row.title}
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
          {row.source}
          <span className="font-num tabular-nums">{row.trade_date}</span>
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

function StockPttList({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.news");
  const [expanded, setExpanded] = useState(false);
  const [sort, setSort] = useState<PttSort>("latest");

  const symbolInfoQuery = useQuery({
    queryKey: queryKeys.symbolRead("symbol_info", symbol, market),
    queryFn: () => getSymbolInfo([symbol], market),
  });
  const companyName = symbolInfoQuery.data?.rows[0]?.name;

  // Server-side body search (`get_ptt_stock_search`) — checks each candidate post's own BODY
  // text, not just its title, across Stock/Tech_Job/Warrant. `enabled` gates on `companyName`
  // resolving first, since the backend route requires it (no server-side name lookup there).
  const searchQuery = useQuery({
    queryKey: queryKeys.read("ptt_stock_search", symbol, companyName, PTT_LOOKBACK_DAYS),
    queryFn: () => getPttStockSearch(symbol, companyName ?? symbol, PTT_LOOKBACK_DAYS),
    enabled: Boolean(companyName),
  });

  const posts = searchQuery.data?.posts ?? [];
  const sorted =
    sort === "hot"
      ? [...posts].sort((a, b) => pushCountValue(b.push_count) - pushCountValue(a.push_count))
      : posts;

  if (symbolInfoQuery.isLoading || searchQuery.isLoading) {
    return <CardSkeletonBody height={480} />;
  }
  if (sorted.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("pttEmpty")}</p>;
  }

  return (
    <>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11.5px] text-[var(--text-muted)] leading-relaxed">
          {t("pttApproximateCaveat")}
        </p>
        <div className="flex shrink-0 gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
          <SortButton
            active={sort === "latest"}
            label={t("latest")}
            onClick={() => setSort("latest")}
          />
          <SortButton active={sort === "hot"} label={t("hot")} onClick={() => setSort("hot")} />
        </div>
      </div>

      <PttPostList posts={sorted.slice(0, PTT_COLLAPSED_ROW_COUNT)} />
      {sorted.length > PTT_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: sorted.length })}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t("ptt")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <PttPostList posts={sorted} />
      </ExpandModal>
    </>
  );
}

function pushCountValue(pushCount: string): number {
  if (pushCount === "爆") return Number.POSITIVE_INFINITY;
  const n = Number.parseInt(pushCount, 10);
  return Number.isNaN(n) ? 0 : n;
}

function SortButton({
  active,
  label,
  onClick,
}: {
  readonly active: boolean;
  readonly label: string;
  readonly onClick: () => void;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
        active
          ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}

function PttPostList({ posts }: { readonly posts: readonly PttStockSearchPost[] }): ReactNode {
  return (
    <ul className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
      {posts.map((post) => (
        <PttPostRow key={post.link} post={post} />
      ))}
    </ul>
  );
}

function PttPostRow({ post }: { readonly post: PttStockSearchPost }): ReactNode {
  const isHot = post.push_count === "爆" || Number(post.push_count) >= 10;
  const href = safeHttpUrl(post.link);
  const body = (
    <>
      <span
        className={cn(
          "mt-0.5 w-fit justify-self-start truncate rounded-[var(--radius-pill)] px-1.5 py-0.5 text-center font-num text-[11px] tabular-nums",
          isHot
            ? "bg-[var(--chart-3)]/15 font-semibold text-[var(--chart-3)]"
            : "bg-[var(--surface-2)] text-[var(--text-muted)]",
        )}
      >
        {post.push_count}
      </span>
      <span className="min-w-0">
        <span className="line-clamp-1 truncate text-[13px] text-[var(--text)] hover:underline">
          {post.post_tag ? (
            <span className="text-[var(--text-muted)]">{post.post_tag} </span>
          ) : null}
          {post.title}
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
          {post.author}
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
          className="grid min-h-11 w-full grid-cols-[40px_1fr] items-start gap-2"
        >
          {body}
        </a>
      ) : (
        <span className="grid min-h-11 w-full grid-cols-[40px_1fr] items-start gap-2">{body}</span>
      )}
    </li>
  );
}
