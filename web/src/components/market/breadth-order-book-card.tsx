"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LockedRowsNote } from "@/components/market/locked-rows-note";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type {
  AdvanceDeclineResponse,
  IndexContributionResponse,
  IndexContributionRow,
} from "@/types/market/breadth";
import type { Market } from "@/types/settings";

const BUCKET_ORDER = [
  "down_over_5pct",
  "down_3_to_5pct",
  "down_2_to_3pct",
  "down_1_to_2pct",
  "down_0_to_1pct",
  "unchanged",
  "up_0_to_1pct",
  "up_1_to_2pct",
  "up_2_to_3pct",
  "up_3_to_5pct",
  "up_over_5pct",
] as const;

const BUCKET_LABEL_KEY: Record<(typeof BUCKET_ORDER)[number], string> = {
  down_over_5pct: "bucketDownOver5",
  down_3_to_5pct: "bucketDown3to5",
  down_2_to_3pct: "bucketDown2to3",
  down_1_to_2pct: "bucketDown1to2",
  down_0_to_1pct: "bucketDown0to1",
  unchanged: "bucketUnchanged",
  up_0_to_1pct: "bucketUp0to1",
  up_1_to_2pct: "bucketUp1to2",
  up_2_to_3pct: "bucketUp2to3",
  up_3_to_5pct: "bucketUp3to5",
  up_over_5pct: "bucketUpOver5",
};

// 5 rows visible per side (positive/negative) fills this card's natural height against its
// taller `AI 多空排行` row-sibling instead of leaving blank space below; "Show all" still expands
// to the full top-10 (`_INDEX_CONTRIBUTION_TOP_N` on the backend) in a modal.
const COLLAPSED_CONTRIBUTOR_COUNT = 5;

/** 漲跌家數 histogram (`get_advance_decline`'s real bucket counts, never fabricated) + 指數貢獻排行
 * (`get_index_contribution`'s APPROXIMATE per-symbol contribution estimate) stacked in one card —
 * both are whole-market breadth/contribution reads for the same trading day, so they share one
 * card rather than each getting their own (mirrors the reference design). The contribution half
 * is Sponsor-tier but opted into governance.md §4's row-preview exception
 * (`supports_row_preview=True`), so an unentitled caller gets real rows with individually
 * `locked` entries beyond `free_row_count` — never a whole-section masked teaser.
 *
 * `indexContribution={null}` (US today) hides that whole section — no real US index is
 * ingested, only the SPY ETF proxy, so there is nothing honest to show there. `market="US"`
 * additionally shows a coverage caveat under the breadth histogram: `us_price_eod`'s own
 * universe is ~1,000-1,500 large/mid-cap symbols by construction
 * (`ingest/us_universe.py`'s own docstring), never the whole exchange the way TW's breadth
 * genuinely is — this label exists so 漲跌家數 for US is never mistaken for whole-market
 * breadth. */
function bucketFill(key: string): string {
  if (key === "unchanged") return "var(--text-muted)";
  return key.startsWith("up") ? "var(--up)" : "var(--down)";
}

export function BreadthOrderBookCard({
  breadth,
  indexContribution,
  market,
}: Readonly<{
  breadth: AdvanceDeclineResponse;
  indexContribution: IndexContributionResponse | null;
  market: Market;
}>): ReactNode {
  const t = useTranslations("market.cards.breadth");
  const tContribution = useTranslations("market.cards.indexContribution");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  // The contribution list is the secondary read on this card: closed by default, one tap to open.

  const bucketData = BUCKET_ORDER.map((key) => ({
    key,
    count: breadth.data_available ? breadth.buckets[key] : 0,
  }));

  const positiveContributors = indexContribution?.positive_contributors ?? [];
  const negativeContributors = indexContribution?.negative_contributors ?? [];
  const hasContributors = positiveContributors.length > 0 || negativeContributors.length > 0;
  const hasMoreContributors =
    positiveContributors.length > COLLAPSED_CONTRIBUTOR_COUNT ||
    negativeContributors.length > COLLAPSED_CONTRIBUTOR_COUNT;

  return (
    <section className="surface-card flex h-full w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      {breadth.data_available ? (
        <div className={cn("flex flex-col", indexContribution === null && "flex-1")}>
          {market === "US" ? (
            <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t("usCoverageCaveat")}</p>
          ) : null}
          <div className="mb-3 flex items-center gap-4 text-[13px]">
            <span style={{ color: "var(--up)" }}>
              {t("advancing")} {formatNumber(breadth.advancing_count, locale)}
            </span>
            <span style={{ color: "var(--down)" }}>
              {t("declining")} {formatNumber(breadth.declining_count, locale)}
            </span>
            <span className="text-[var(--text-muted)]">
              {t("unchanged")} {formatNumber(breadth.unchanged_count, locale)}
            </span>
          </div>
          <div
            className={cn(indexContribution === null && "flex-1")}
            style={{ width: "100%", height: indexContribution === null ? undefined : 160 }}
          >
            <ResponsiveContainer>
              <BarChart data={bucketData} margin={{ left: 0, right: 8 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="key"
                  tickFormatter={(key: string) =>
                    t(BUCKET_LABEL_KEY[key as (typeof BUCKET_ORDER)[number]])
                  }
                  tick={{ fill: "var(--text-muted)", fontSize: 10 }}
                  stroke="var(--border)"
                />
                <YAxis
                  tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                  width={36}
                  stroke="var(--border)"
                />
                <Tooltip
                  // A faint wash behind the hovered column, not Recharts' default light-grey slab,
                  // which is a harsh block on the dark theme and hides the bar's own colour.
                  cursor={{ fill: "var(--text)", opacity: 0.08 }}
                  contentStyle={{
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-input)",
                    color: "var(--text)",
                  }}
                  labelStyle={{ color: "var(--text)" }}
                  itemStyle={{ color: "var(--text)" }}
                  labelFormatter={(key) =>
                    t(BUCKET_LABEL_KEY[key as (typeof BUCKET_ORDER)[number]])
                  }
                  formatter={(value) => formatNumber(value as number, locale)}
                />
                <Bar dataKey="count" name={t("breadthCountSeries")} radius={3}>
                  {bucketData.map((point) => (
                    <Cell key={point.key} fill={bucketFill(point.key)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <AdvanceDeclineSummaryBar breadth={breadth} locale={locale} />
        </div>
      ) : (
        <p className="text-[13px] text-[var(--text-muted)]">{t("breadthUnavailable")}</p>
      )}

      {indexContribution ? (
        <div className="mt-4 border-[var(--border)] border-t pt-3">
          <div className="flex min-h-11 items-center gap-2">
            <h3 className="font-medium text-[13px] text-[var(--text)]">{tContribution("title")}</h3>
            <span className="rounded-[var(--radius-pill)] bg-[var(--warn-weak)] px-2 py-0.5 text-[11px] text-[var(--warn)]">
              {tContribution("estimateBadge")}
            </span>
          </div>

          <div className="pt-1">
            <p className="mb-3 text-[11px] text-[var(--text-muted)]">
              {tContribution("estimateNote")}
            </p>
            {hasContributors ? (
              <>
                <IndexContributionColumns
                  positiveContributors={positiveContributors.slice(0, COLLAPSED_CONTRIBUTOR_COUNT)}
                  negativeContributors={negativeContributors.slice(0, COLLAPSED_CONTRIBUTOR_COUNT)}
                  positiveLabel={tContribution("positive")}
                  negativeLabel={tContribution("negative")}
                />

                {hasMoreContributors ? (
                  <button
                    type="button"
                    onClick={() => setExpanded(true)}
                    className={cn(
                      "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                      "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                    )}
                  >
                    <span>
                      {t("expandContribution", {
                        count: Math.max(positiveContributors.length, negativeContributors.length),
                      })}
                    </span>
                  </button>
                ) : null}
              </>
            ) : (
              <p className="text-[13px] text-[var(--text-muted)]">{tContribution("unavailable")}</p>
            )}

            {indexContribution.trade_date ? (
              <p className="mt-3 text-[11px] text-[var(--text-muted)]">
                {tContribution("updatedAt", { date: indexContribution.trade_date })}
              </p>
            ) : null}
          </div>

          <ExpandModal
            open={expanded}
            title={tContribution("title")}
            closeLabel={t("collapseContribution")}
            onClose={() => setExpanded(false)}
          >
            <IndexContributionColumns
              positiveContributors={positiveContributors}
              negativeContributors={negativeContributors}
              positiveLabel={tContribution("positive")}
              negativeLabel={tContribution("negative")}
            />
          </ExpandModal>
        </div>
      ) : null}
    </section>
  );
}

/** Proportional advancing/unchanged/declining summary bar — segment widths reflect each
 * count's share of the whole market, mirroring the reference design's stacked total-breadth
 * bar underneath the per-bucket histogram (the histogram alone doesn't convey the overall
 * up-vs-down split at a glance). */
function AdvanceDeclineSummaryBar({
  breadth,
  locale,
}: Readonly<{
  breadth: AdvanceDeclineResponse;
  locale: string;
}>): ReactNode {
  const total = breadth.advancing_count + breadth.declining_count + breadth.unchanged_count;
  if (total === 0) {
    return null;
  }
  const advancingPct = (breadth.advancing_count / total) * 100;
  const decliningPct = (breadth.declining_count / total) * 100;
  const unchangedPct = 100 - advancingPct - decliningPct;

  return (
    <div className="mt-4">
      <div className="flex h-2 w-full overflow-hidden rounded-[var(--radius-pill)]">
        <div style={{ width: `${advancingPct}%`, backgroundColor: "var(--up)" }} />
        <div style={{ width: `${unchangedPct}%`, backgroundColor: "var(--text-muted)" }} />
        <div style={{ width: `${decliningPct}%`, backgroundColor: "var(--down)" }} />
      </div>
      <div className="mt-1.5 flex items-center justify-between font-num text-[13px] tabular-nums">
        <span style={{ color: "var(--up)" }}>
          ▲ {formatNumber(breadth.advancing_count, locale)}
        </span>
        <span style={{ color: "var(--down)" }}>
          ▼ {formatNumber(breadth.declining_count, locale)}
        </span>
      </div>
    </div>
  );
}

function IndexContributionColumns({
  positiveContributors,
  negativeContributors,
  positiveLabel,
  negativeLabel,
}: Readonly<{
  positiveContributors: readonly IndexContributionRow[];
  negativeContributors: readonly IndexContributionRow[];
  positiveLabel: string;
  negativeLabel: string;
}>): ReactNode {
  return (
    <div className="grid grid-cols-2 gap-4">
      <IndexContributionColumn
        label={positiveLabel}
        rows={positiveContributors}
        color="var(--up)"
      />
      <IndexContributionColumn
        label={negativeLabel}
        rows={negativeContributors}
        color="var(--down)"
      />
    </div>
  );
}

function IndexContributionColumn({
  label,
  rows,
  color,
}: Readonly<{
  label: string;
  rows: readonly IndexContributionRow[];
  color: string;
}>): ReactNode {
  return (
    <div>
      <p className="mb-2 text-[11px] text-[var(--text-muted)]">{label}</p>
      <ul className="flex flex-col gap-1.5">
        {rows.map((row) =>
          row.locked ? (
            <LockedIndexContributionRow key={row.rank} rank={row.rank} color={color} />
          ) : (
            <IndexContributionRowItem key={row.symbol} row={row} color={color} />
          ),
        )}
      </ul>
      <LockedRowsNote rows={rows} />
    </div>
  );
}

/** governance.md §4 top-N-preview exception's row-level lock: every field but `rank` is nulled
 * server-side for a `locked` row — mirrors `daily-signal-table.tsx`'s own locked-row pattern for
 * this column's row shape. */
function LockedIndexContributionRow({
  rank,
  color,
}: {
  readonly rank: number;
  readonly color: string;
}): ReactNode {
  return (
    <li
      className="flex items-center gap-2 rounded-[var(--radius-input)] px-3 py-2"
      style={{ backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)` }}
    >
      <span className="w-4 shrink-0 text-[12px] text-[var(--text-muted)]">{rank}</span>
      <span className="flex flex-1 items-center gap-1.5 truncate text-[13px] text-[var(--text-muted)]">
        <LockIcon size={11} weight="regular" aria-hidden />
        <span aria-hidden>••••</span>
      </span>
      <span
        aria-hidden
        className="shrink-0 font-num text-[13px] text-[var(--text-muted)] tabular-nums"
      >
        ••••
      </span>
    </li>
  );
}

/** One real (never-locked) row — split out of `IndexContributionColumn` so that component's own
 * `.map` stays a simple locked/unlocked branch. */
function IndexContributionRowItem({
  row,
  color,
}: {
  readonly row: IndexContributionRow;
  readonly color: string;
}): ReactNode {
  const locale = useLocale();
  const value = row.contribution_estimate ?? 0;
  return (
    <li
      className="flex items-center gap-2 rounded-[var(--radius-input)] px-3 py-2"
      style={{ backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)` }}
    >
      <span className="w-4 shrink-0 text-[12px] text-[var(--text-muted)]">{row.rank}</span>
      <span className="flex-1 truncate text-[13px] text-[var(--text)]">
        {row.stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
      </span>
      <span className="shrink-0 font-num text-[13px] tabular-nums" style={{ color }}>
        {value >= 0 ? "+" : ""}
        {formatNumber(value, locale, { decimals: 2 })}
      </span>
    </li>
  );
}
