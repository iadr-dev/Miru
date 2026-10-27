"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryState } from "@/components/ui/query-state";
import { getPttBoard } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { cn } from "@/lib/cn";
import { safeHttpUrl } from "@/lib/safe-url";
import {
  PTT_BOARDS,
  type PttBoardName,
  type PttBoardResponse,
  type PttPost,
} from "@/types/market/news";

// This sub-tab has no sibling card next to it, so its own post list needs to fill the tab's
// width/height on its own — 40 items across a 2-column grid, same reasoning
// `NEWS_FEED_COLLAPSED_ROW_COUNT` documents for its own sub-tab.
const PTT_BOARD_COLLAPSED_ROW_COUNT = 40;

/**
 * PTT — the TW tab's PTT sub-tab, over all 5 boards `tools.ptt_tools.PTT_BOARDS` covers
 * (Stock/Tech_Job/Warrant → `stock_buzz`, usable-in-analysis buzz signal; Beauty/Gossiping →
 * `offtopic`, never a market claim). Public (`DataClass.PUBLIC`). The push-count "爆" badge uses
 * `--chart-3` (a categorical intensity marker, never `--up`/`--down` — those are reserved for
 * signed price/%/score deltas, web/OVERVIEW.md §4).
 */
export function PttBoardPanel(): ReactNode {
  const t = useTranslations("market.cards.pttBoard");
  const [board, setBoard] = useState<PttBoardName>("Stock");
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: queryKeys.read("ptt_board", board),
    queryFn: () => getPttBoard(board),
  });

  return (
    <section className="surface-card flex min-h-[70vh] w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-1.5">
        {PTT_BOARDS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={board === key}
            onClick={() => setBoard(key)}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              board === key
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`board.${key}`)}
          </button>
        ))}
      </div>

      <div className="mt-3">
        <QueryState query={query} skeleton={<CardSkeletonBody height={600} />}>
          {(data) => (
            <PttBoardContent data={data} expanded={expanded} onExpandedChange={setExpanded} />
          )}
        </QueryState>
      </div>
    </section>
  );
}

/** The three states of one board: posts present, source down (an outage is NOT "nothing
 * posted" — an empty list from an unreachable/unavailable source says so), or genuinely no posts. */
export function PttBoardContent({
  data,
  expanded,
  onExpandedChange,
}: {
  readonly data: PttBoardResponse;
  readonly expanded: boolean;
  readonly onExpandedChange: (next: boolean) => void;
}): ReactNode {
  const t = useTranslations("market.cards.pttBoard");

  if (data.posts.length === 0) {
    if (data.health_status === "ok" || data.health_status === "stale") {
      return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
    }
    return <EmptyState compact title={t("unavailable")} />;
  }
  const boardTag = data.posts[0]?.board_tag ?? "stock_buzz";

  return (
    <>
      <p className="mb-2 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
        {boardTag === "offtopic" ? t("offtopicCaveat") : t("buzzCaveat")}
      </p>

      <PttPostList posts={data.posts.slice(0, PTT_BOARD_COLLAPSED_ROW_COUNT)} />

      {data.posts.length > PTT_BOARD_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => onExpandedChange(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{t("expandTo", { count: data.posts.length })}</span>
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => onExpandedChange(false)}
      >
        <PttPostList posts={data.posts} />
      </ExpandModal>
    </>
  );
}

function PttPostList({ posts }: { readonly posts: readonly PttPost[] }): ReactNode {
  // Same dedupe-by-link reasoning as `NewsFeedList` — a board's index-listing pages can surface
  // the same post twice (a Re: thread bumping the original back onto the visible index window).
  const deduped = Array.from(new Map(posts.map((post) => [post.link, post])).values());

  return (
    <ul className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
      {deduped.map((post) => (
        <PttPostRow key={post.link} post={post} />
      ))}
    </ul>
  );
}

function PttPostRow({ post }: { readonly post: PttPost }): ReactNode {
  const isHot = post.push_count === "爆" || Number(post.push_count) >= 10;
  // `post.link` is third-party PTT content, never trusted as-is (web/OVERVIEW.md §8) — reject a
  // `javascript:`/`data:` URI before it ever reaches an `href`.
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
            <span className="text-[var(--text-muted)]">[{post.post_tag}] </span>
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
