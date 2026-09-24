"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getActiveEtfReport } from "@/lib/api/etf-detail";
import { cn } from "@/lib/cn";
import type { ActiveEtfHoldingWeightRow } from "@/types/etf-detail";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

const DONUT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
] as const;
const OTHERS_COLOR = "var(--text-muted)";
const TOP_N = 4;
const SUB_TABS = ["holdings", "industry"] as const;
type SubTab = (typeof SUB_TABS)[number];
const DATA_SHEET_COLLAPSED_ROW_COUNT = 10;

type Slice = { label: string; weight: number; color: string };

/** 成分股 / 產業分佈 — genuinely absent for passive TW ETFs today (`sources/etf_pcf.py`'s own
 * docstring: per-constituent holdings are never ingested for passive/leveraged/inverse funds).
 * Scoped to active-management ETFs only, reusing `get_active_etf_report`'s full current-day
 * `current_holdings` snapshot (weight + industry per constituent) — the same honest-scoping
 * precedent `tracker-panel.tsx` already established for the reverse (stock→ETF) relationship.
 * A passive ETF symbol renders a calm coverage-class empty state, never a fabricated donut. */
export function HoldingsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const [subTab, setSubTab] = useState<SubTab>("holdings");
  const query = useQuery({
    queryKey: ["active_etf_report", symbol, market],
    queryFn: () => getActiveEtfReport(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={360} />
      </section>
    );
  }
  if (!query.data) return null;

  if (isMaskedTeaser(query.data)) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <SponsorTeaser
          headline={t("title")}
          preview={query.data.preview}
          requiredTier={query.data.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </section>
    );
  }

  const data = query.data;
  if (data.report_date == null || data.current_holdings.length === 0) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)] leading-[1.6]">
          {t("passiveUnavailable")}
        </p>
      </section>
    );
  }

  const holdingSlices = topSlices(
    data.current_holdings.map((row) => ({ label: row.component_stock_name, weight: row.weight })),
    t("others"),
  );
  const industrySlices = topSlices(industryWeights(data.current_holdings), t("others"));

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <div className="mb-3 flex items-baseline justify-between gap-2">
          <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("asOfDate", { date: data.report_date })}
          </span>
        </div>
        <p className="mb-4 text-[11px] text-[var(--text-muted)] leading-[1.5]">
          {t("activeOnlyCaveat")}
        </p>
        <div
          role="tablist"
          aria-label={t("subTabLabel")}
          className="mb-4 flex w-fit gap-1 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface-2)] p-1"
        >
          {SUB_TABS.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={subTab === key}
              onClick={() => setSubTab(key)}
              className={cn(
                "min-h-8 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
                subTab === key
                  ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(`subTab.${key}`)}
            </button>
          ))}
        </div>
        <DonutAndTable slices={subTab === "holdings" ? holdingSlices : industrySlices} />
      </section>
      {subTab === "holdings" ? (
        <HoldingsDataSheetCard holdings={data.current_holdings} />
      ) : (
        <IndustryDataSheetCard rows={industryWeights(data.current_holdings)} />
      )}
    </div>
  );
}

function HoldingsDataSheetCard({
  holdings,
}: {
  readonly holdings: readonly ActiveEtfHoldingWeightRow[];
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const [expanded, setExpanded] = useState(false);
  const ranked = [...holdings].sort((a, b) => b.weight - a.weight);

  return (
    <DataSheetCard
      title={t("dataSheetTitle")}
      totalCount={ranked.length}
      expanded={expanded}
      onExpand={() => setExpanded(true)}
      onCollapse={() => setExpanded(false)}
    >
      <HoldingsTable rows={ranked.slice(0, DATA_SHEET_COLLAPSED_ROW_COUNT)} />
      <ExpandModal
        open={expanded}
        title={t("dataSheetTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <HoldingsTable rows={ranked} />
      </ExpandModal>
    </DataSheetCard>
  );
}

function HoldingsTable({
  rows,
}: {
  readonly rows: readonly ActiveEtfHoldingWeightRow[];
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const locale = useLocale();

  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
          <th className="py-2 text-left font-medium">{t("column.rank")}</th>
          <th className="py-2 text-left font-medium">{t("column.stock")}</th>
          <th className="py-2 text-left font-medium">{t("column.industry")}</th>
          <th className="py-2 text-right font-medium">{t("column.weight")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.component_stock_id} className="border-[var(--border)] border-b">
            <td className="py-2 text-[var(--text-muted)] tabular-nums">{index + 1}</td>
            <td className="py-2 text-[var(--text)]">
              {row.component_stock_name}
              <span className="ml-1 font-num text-[11px] text-[var(--text-muted)]">
                {row.component_stock_id}
              </span>
            </td>
            <td className="py-2 text-[var(--text-muted)]">{row.industry_category ?? "—"}</td>
            <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
              {formatNumber(row.weight, locale, { decimals: 2 })}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function IndustryDataSheetCard({
  rows,
}: {
  readonly rows: readonly { label: string; weight: number }[];
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const [expanded, setExpanded] = useState(false);
  const ranked = [...rows].sort((a, b) => b.weight - a.weight);

  return (
    <DataSheetCard
      title={t("dataSheetTitle")}
      totalCount={ranked.length}
      expanded={expanded}
      onExpand={() => setExpanded(true)}
      onCollapse={() => setExpanded(false)}
    >
      <IndustryTable rows={ranked.slice(0, DATA_SHEET_COLLAPSED_ROW_COUNT)} />
      <ExpandModal
        open={expanded}
        title={t("dataSheetTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <IndustryTable rows={ranked} />
      </ExpandModal>
    </DataSheetCard>
  );
}

function IndustryTable({
  rows,
}: {
  readonly rows: readonly { label: string; weight: number }[];
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const locale = useLocale();

  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
          <th className="py-2 text-left font-medium">{t("column.rank")}</th>
          <th className="py-2 text-left font-medium">{t("column.industry")}</th>
          <th className="py-2 text-right font-medium">{t("column.weight")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.label} className="border-[var(--border)] border-b">
            <td className="py-2 text-[var(--text-muted)] tabular-nums">{index + 1}</td>
            <td className="py-2 text-[var(--text)]">{row.label}</td>
            <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
              {formatNumber(row.weight, locale, { decimals: 2 })}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DataSheetCard({
  title,
  totalCount,
  expanded,
  onExpand,
  children,
}: {
  readonly title: string;
  readonly totalCount: number;
  readonly expanded: boolean;
  readonly onExpand: () => void;
  readonly onCollapse: () => void;
  readonly children: ReactNode;
}): ReactNode {
  const t = useTranslations("etf.holdings");

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{title}</h2>
      <div className="overflow-x-auto">{children}</div>
      {totalCount > DATA_SHEET_COLLAPSED_ROW_COUNT && !expanded ? (
        <button
          type="button"
          onClick={onExpand}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{t("expandTo", { count: totalCount })}</span>
        </button>
      ) : null}
    </section>
  );
}

function topSlices(
  rows: readonly { label: string; weight: number }[],
  othersLabel: string,
): Slice[] {
  const sorted = [...rows].sort((a, b) => b.weight - a.weight);
  const top = sorted.slice(0, TOP_N).map((row, index) => ({
    label: row.label,
    weight: row.weight,
    color: DONUT_COLORS[index % DONUT_COLORS.length] ?? "var(--chart-1)",
  }));
  const othersWeight = sorted.slice(TOP_N).reduce((sum, row) => sum + row.weight, 0);
  return othersWeight > 0
    ? [...top, { label: othersLabel, weight: othersWeight, color: OTHERS_COLOR }]
    : top;
}

function industryWeights(
  rows: readonly ActiveEtfHoldingWeightRow[],
): { label: string; weight: number }[] {
  const byIndustry = new Map<string, number>();
  for (const row of rows) {
    const key = row.industry_category ?? "";
    if (!key) continue;
    byIndustry.set(key, (byIndustry.get(key) ?? 0) + row.weight);
  }
  return [...byIndustry.entries()].map(([label, weight]) => ({ label, weight }));
}

function DonutAndTable({ slices }: { readonly slices: readonly Slice[] }): ReactNode {
  const locale = useLocale();

  if (slices.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <div className="h-[180px] w-[180px] shrink-0">
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={slices}
              dataKey="weight"
              nameKey="label"
              innerRadius="55%"
              outerRadius="100%"
              strokeWidth={0}
            >
              {slices.map((slice) => (
                <Cell key={slice.label} fill={slice.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              itemStyle={{ color: "var(--text)" }}
              formatter={(value) => [
                `${formatNumber(value as number, locale, { decimals: 2 })}%`,
                "",
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-1 flex-col gap-2">
        {slices.map((slice) => (
          <div key={slice.label} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-[13px] text-[var(--text)]">
              <span
                aria-hidden
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: slice.color }}
              />
              {slice.label}
            </span>
            <span
              className="shrink-0 font-num text-[13px] tabular-nums"
              style={{ color: slice.color }}
            >
              {formatNumber(slice.weight, locale, { decimals: 2 })}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
