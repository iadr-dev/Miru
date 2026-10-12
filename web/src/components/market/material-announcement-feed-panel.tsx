"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  MATERIAL_ANNOUNCEMENT_CATEGORY_COLORS,
  MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT,
  MaterialAnnouncementFeedTable,
} from "@/components/market/material-announcement-feed-table";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryState } from "@/components/ui/query-state";
import { getMaterialAnnouncementFeed } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

/**
 * 重大訊息 — the TW tab's 每日焦點 sub-tab card for the whole-market MOPS material-announcement
 * feed. Public (`DataClass.PUBLIC`) — no `SponsorTeaser`/masked-teaser branch, per this
 * feature's own explicit no-blur requirement (unlike every Sponsor-tier card elsewhere in this
 * dashboard).
 *
 * **History grows day by day.** MOPS's own upstream has no date parameter — ingest can only
 * ever capture "today's" dump — so the date-tab row may show a single day for a while after
 * this ships, growing by one entry per successful daily ingest run. No padded/disabled
 * placeholder tabs; this is not a bug, see the backing tool's own docstring.
 */
const MATERIAL_ANNOUNCEMENT_FEED_DATE_TAB_COUNT = 7;

export function MaterialAnnouncementFeedPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.materialAnnouncementFeed");
  const [tradeDate, setTradeDate] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: ["material_announcement_feed", market, tradeDate, category],
    queryFn: () => getMaterialAnnouncementFeed(market, tradeDate, category ?? undefined),
  });

  return (
    <section className="flex w-full flex-col surface-card p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={380} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <p className="text-[13px] text-[var(--text-muted)]">
                {data.unavailable_reason ?? t("unavailable")}
              </p>
            );
          }
          const totalCount = Object.values(data.category_counts).reduce((a, b) => a + b, 0);

          return (
            <>
              <div className="flex flex-wrap gap-1.5">
                <CategoryChip
                  active={category === null}
                  label={t("all")}
                  count={totalCount}
                  onClick={() => setCategory(null)}
                />
                {Object.entries(data.category_counts).map(([key, count]) => (
                  <CategoryChip
                    key={key}
                    active={category === key}
                    label={key}
                    count={count}
                    color={MATERIAL_ANNOUNCEMENT_CATEGORY_COLORS[key]}
                    onClick={() => setCategory(key)}
                  />
                ))}
              </div>

              {data.available_dates.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {[...data.available_dates]
                    .sort()
                    .reverse()
                    .slice(0, MATERIAL_ANNOUNCEMENT_FEED_DATE_TAB_COUNT)
                    .map((date) => (
                      <button
                        key={date}
                        type="button"
                        onClick={() => setTradeDate(date)}
                        aria-pressed={(tradeDate ?? data.report_date) === date}
                        className={cn(
                          "rounded-[var(--radius-pill)] px-2.5 py-1 font-num text-[11px] tabular-nums transition-colors",
                          (tradeDate ?? data.report_date) === date
                            ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
                        )}
                      >
                        {date}
                      </button>
                    ))}
                </div>
              ) : null}

              <div className="mt-3">
                <MaterialAnnouncementFeedTable
                  rows={data.rows.slice(0, MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT)}
                />
              </div>

              {data.rows.length > MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: data.rows.length })}</span>
                </button>
              ) : null}

              <ExpandModal
                open={expanded}
                title={t("title")}
                closeLabel={t("collapse")}
                onClose={() => setExpanded(false)}
              >
                <MaterialAnnouncementFeedTable rows={data.rows} />
              </ExpandModal>
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

function CategoryChip({
  active,
  label,
  count,
  color,
  onClick,
}: {
  readonly active: boolean;
  readonly label: string;
  readonly count: number;
  readonly color?: string;
  readonly onClick: () => void;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={active && color ? { backgroundColor: color, color: "#06231F" } : undefined}
      className={cn(
        "rounded-[var(--radius-pill)] px-3 py-1 text-[12px] transition-colors",
        active
          ? color
            ? "font-semibold"
            : "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
          : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label} {count}
    </button>
  );
}
