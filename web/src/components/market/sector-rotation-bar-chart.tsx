"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
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
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { SectorRotationDayRow } from "@/types/market";

// Same collapsed-row-count convention every other market card in this dashboard uses.
const TABLE_COLLAPSED_ROW_COUNT = 10;

/** Filters to the latest `trade_date` present and sorts descending by `net_flow` — the exact
 * ranking this bar chart (and its table fallback) renders. */
export function latestRankedByNetFlow(
  rows: readonly SectorRotationDayRow[],
  tradeDates: readonly string[],
): SectorRotationDayRow[] {
  const latestDate = tradeDates.at(-1);
  return rows
    .filter((row) => row.trade_date === latestDate)
    .sort((a, b) => b.net_flow - a.net_flow);
}

/** Takes the top `halfCount` net-inflow rows and the top `halfCount` net-outflow rows from an
 * already-`net_flow`-descending-sorted list, re-merged in descending order — guarantees the
 * collapsed preview always shows both `--up` and `--down` bars (a plain top-N slice would be
 * all-inflow whenever more than N industries are net-inflow that day, which real market data
 * often is). `rows` must already be sorted descending by `net_flow` (as `latestRankedByNetFlow`
 * returns). */
export function topInflowAndOutflow(
  rows: readonly SectorRotationDayRow[],
  halfCount = TABLE_COLLAPSED_ROW_COUNT / 2,
): SectorRotationDayRow[] {
  const inflow = rows.slice(0, halfCount);
  const outflow = rows.slice(-halfCount).filter((row) => !inflow.includes(row));
  return [...inflow, ...outflow];
}

/**
 * 板塊輪動 bar-chart view — one bar per industry, showing the latest trading day's `net_flow`
 * (net institutional flow that day), ranked descending. Unlike the former rank-bump chart, this
 * IS a signed money figure, so it correctly uses `--up`/`--down` (never `--chart-1..6`) per
 * 紅漲綠跌 governance — a net-inflow industry's bar is `--up`, net-outflow is `--down`.
 *
 * Every industry actually present is listed in the data-table fallback below, collapsed to the
 * top `TABLE_COLLAPSED_ROW_COUNT` inline with the same "展開完整 N 筆" `ExpandModal` pattern every
 * other ranked-list market card in this dashboard follows.
 */
export function SectorRotationBarChart({
  rows,
  tradeDates,
}: {
  readonly rows: readonly SectorRotationDayRow[];
  readonly tradeDates: readonly string[];
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");
  const [expanded, setExpanded] = useState(false);

  const latestRows = useMemo(() => latestRankedByNetFlow(rows, tradeDates), [rows, tradeDates]);
  // The chart shows only the collapsed slice, in lockstep with the table below it — Recharts
  // skips category-axis tick labels once rows outgrow the available height (confirmed live: with
  // all ~40 industries plotted at a fixed 320px height, most bars rendered with NO adjacent label
  // at all, silently misaligning the labels that DID render against the wrong bars). The
  // `ExpandModal` renders every row in a chart tall enough to give each one its own tick, so
  // "展開完整 N 筆" still reaches the full data, just at a height where every label fits.
  //
  // A plain top-N slice would be all-inflow (or all-outflow) whenever one side has more than N
  // industries on it — the collapsed preview must show BOTH `--up`/`--down` extremes, same
  // top-inflow/top-outflow split `get_index_contribution`'s own 正貢獻/負貢獻 card already uses,
  // not just "biggest N by signed value".
  const collapsedRows = useMemo(() => topInflowAndOutflow(latestRows), [latestRows]);

  if (rows.length === 0 || tradeDates.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("rotationUnavailable")}</p>;
  }

  return (
    <div>
      <SectorRotationBars rows={collapsedRows} height={320} locale={locale} t={t} />

      {/* Screen-reader / keyboard fallback: every industry actually present, not just what fits
       * in the chart's viewport height. Collapses to the top `TABLE_COLLAPSED_ROW_COUNT` inline,
       * same "展開完整 N 筆" pattern every other ranked-list market card in this dashboard uses. */}
      <SectorRotationBarTable rows={collapsedRows} />
      {latestRows.length > TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{t("expandTo", { count: latestRows.length })}</span>
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t("rotationTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <SectorRotationBars
          rows={latestRows}
          height={Math.max(latestRows.length * ROW_HEIGHT_PX, 320)}
          locale={locale}
          t={t}
        />
        <SectorRotationBarTable rows={latestRows} />
      </ExpandModal>
    </div>
  );
}

// Enough vertical room per bar for its category-axis label to render on its own line — the
// exact height that was missing at the fixed 320px container when every industry was plotted.
const ROW_HEIGHT_PX = 28;

function SectorRotationBars({
  rows,
  height,
  locale,
  t,
}: {
  readonly rows: readonly SectorRotationDayRow[];
  readonly height: number;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
          <CartesianGrid horizontal={false} stroke="var(--grid)" />
          <XAxis
            type="number"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickFormatter={(v: number) => formatCompactNumber(v, locale)}
            stroke="var(--border)"
          />
          <YAxis
            type="category"
            dataKey="industry"
            interval={0}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            width={96}
            stroke="var(--border)"
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-input)",
              color: "var(--text)",
            }}
            formatter={(value) => [formatNumber(value as number, locale), t("netFlowLabel")]}
          />
          <Bar dataKey="net_flow" radius={3} isAnimationActive={false}>
            {rows.map((row) => (
              <Cell key={row.industry} fill={row.net_flow >= 0 ? "var(--up)" : "var(--down)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The net-flow data table, shared verbatim between the card's inline (sliced) preview and the
 * `ExpandModal` full list — also reused as-is by `SectorRotationParticleSphere`, whose own visual
 * is `aria-hidden` (a rotating decorative sphere has no meaningful DOM structure for a screen
 * reader), so this table is that view's ONLY accessible representation of the data, not just a
 * keyboard/no-JS fallback. */
export function SectorRotationBarTable({
  rows,
}: {
  readonly rows: readonly SectorRotationDayRow[];
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");

  return (
    <div className="mt-3 overflow-x-auto">
      <table className="min-w-full text-[12px]">
        <caption className="sr-only">{t("barTableCaption")}</caption>
        <thead>
          <tr>
            <th scope="col" className="px-2 py-1 text-left text-[var(--text-muted)]">
              {t("columnIndustry")}
            </th>
            <th scope="col" className="px-2 py-1 text-right text-[var(--text-muted)]">
              {t("netFlowLabel")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const up = row.net_flow >= 0;
            return (
              <tr key={row.industry} className="border-[var(--border)] border-t">
                <th scope="row" className="px-2 py-1 text-left font-normal text-[var(--text)]">
                  {row.industry}
                </th>
                <td
                  className="px-2 py-1 text-right font-num tabular-nums"
                  style={{ color: up ? "var(--up)" : "var(--down)" }}
                >
                  {up ? "▲" : "▼"}
                  {formatNumber(Math.abs(row.net_flow), locale)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
