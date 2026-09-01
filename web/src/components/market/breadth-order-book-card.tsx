"use client";

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
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type {
  AdvanceDeclineResponse,
  IndexContributionResponse,
  IndexContributionRow,
} from "@/types/market";
import { isMaskedTeaser } from "@/types/market";

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

// Matches the reference layout: 3 rows visible per side (positive/negative), "Show all" expands
// to the full top-10 (`_INDEX_CONTRIBUTION_TOP_N` on the backend) in a modal.
const COLLAPSED_CONTRIBUTOR_COUNT = 3;

/** 漲跌家數 histogram (`get_advance_decline`'s real bucket counts, never fabricated) + 指數貢獻排行
 * (`get_index_contribution`'s APPROXIMATE per-symbol contribution estimate) stacked in one card —
 * both are whole-market breadth/contribution reads for the same trading day, so they share one
 * card rather than each getting their own (mirrors the reference design). Only the contribution
 * half is Sponsor-tier, so the masked-teaser branch is handled HERE (blurring only that
 * sub-section via `SponsorTeaser`), not by the caller — the breadth histogram above it is public
 * and must never be blurred along with it. */
export function BreadthOrderBookCard({
  breadth,
  indexContribution,
}: {
  breadth: AdvanceDeclineResponse;
  indexContribution: IndexContributionResponse;
}): ReactNode {
  const t = useTranslations("market.cards.breadth");
  const tContribution = useTranslations("market.cards.indexContribution");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);

  const bucketData = BUCKET_ORDER.map((key) => ({
    key,
    count: breadth.data_available ? breadth.buckets[key] : 0,
  }));

  const locked = isMaskedTeaser(indexContribution);
  const positiveContributors = locked ? [] : indexContribution.positive_contributors;
  const negativeContributors = locked ? [] : indexContribution.negative_contributors;
  const hasContributors = positiveContributors.length > 0 || negativeContributors.length > 0;
  const hasMoreContributors =
    positiveContributors.length > COLLAPSED_CONTRIBUTOR_COUNT ||
    negativeContributors.length > COLLAPSED_CONTRIBUTOR_COUNT;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      {breadth.data_available ? (
        <>
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
          <div style={{ width: "100%", height: 160 }}>
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
                  contentStyle={{
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-input)",
                    color: "var(--text)",
                  }}
                  labelFormatter={(key) =>
                    t(BUCKET_LABEL_KEY[key as (typeof BUCKET_ORDER)[number]])
                  }
                  formatter={(value) => formatNumber(value as number, locale)}
                />
                <Bar dataKey="count" radius={3}>
                  {bucketData.map((point) => (
                    <Cell
                      key={point.key}
                      fill={
                        point.key === "unchanged"
                          ? "var(--text-muted)"
                          : point.key.startsWith("up")
                            ? "var(--up)"
                            : "var(--down)"
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      ) : (
        <p className="text-[13px] text-[var(--text-muted)]">{t("breadthUnavailable")}</p>
      )}

      <div className="mt-4 border-[var(--border)] border-t pt-3">
        <div className="mb-1 flex items-center gap-2">
          <h3 className="font-medium text-[13px] text-[var(--text)]">{tContribution("title")}</h3>
          <span className="rounded-[var(--radius-pill)] bg-[var(--warn)]/15 px-2 py-0.5 text-[11px] text-[var(--warn)]">
            {tContribution("estimateBadge")}
          </span>
        </div>

        {locked ? (
          <SponsorTeaser
            headline={tContribution("title")}
            preview={indexContribution.preview}
            requiredTier={indexContribution.required_tier}
          >
            <div className="h-40 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
          </SponsorTeaser>
        ) : (
          <>
            <p className="mb-3 text-[11px] text-[var(--text-muted)]">
              {indexContribution.contribution_caveat}
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
          </>
        )}
      </div>
    </section>
  );
}

function IndexContributionColumns({
  positiveContributors,
  negativeContributors,
  positiveLabel,
  negativeLabel,
}: {
  positiveContributors: readonly IndexContributionRow[];
  negativeContributors: readonly IndexContributionRow[];
  positiveLabel: string;
  negativeLabel: string;
}): ReactNode {
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
}: {
  label: string;
  rows: readonly IndexContributionRow[];
  color: string;
}): ReactNode {
  return (
    <div>
      <p className="mb-2 text-[11px] text-[var(--text-muted)]">{label}</p>
      <ul className="flex flex-col gap-1.5">
        {rows.map((row, index) => (
          <li
            key={row.symbol}
            className="flex items-center gap-2 rounded-[var(--radius-input)] px-3 py-2"
            style={{ backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)` }}
          >
            <span className="w-4 shrink-0 text-[12px] text-[var(--text-muted)]">{index + 1}</span>
            <span className="flex-1 truncate text-[13px] text-[var(--text)]">{row.stock_name}</span>
            <span className="shrink-0 font-num text-[13px] tabular-nums" style={{ color }}>
              {row.contribution_estimate >= 0 ? "+" : ""}
              {formatNumber(row.contribution_estimate, "zh-TW", { decimals: 2 })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
