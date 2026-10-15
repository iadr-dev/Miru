"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { DividendPolicyCard } from "@/components/stock/dividend-policy-card";
import { RangeSelect } from "@/components/stock/range-select";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { getFinancialStatement, getMonthRevenue } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { PREVIEW_ROWS } from "@/lib/density";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionColor, directionGlyph } from "@/lib/signed";
import type { Market } from "@/types/settings";
import {
  FINANCIAL_STATEMENT_TYPES,
  type FinancialStatementLine,
  type FinancialStatementType,
  type MonthRevenueTrendPoint,
} from "@/types/stock/fundamentals";

type PeriodType = FinancialStatementLine["period_type"];
const PERIOD_TYPES: readonly PeriodType[] = ["annual", "quarterly", "ttm"];

// The extended `--chart-1..10` categorical set (tokens.css) — 10 slots so this chart's 10
// tracked line items never repeat a color, indexed directly (not hashed) so the Nth item always
// gets the Nth token and the legend order matches the color order — a hash-based pick
// (`lib/chart-token-hash.ts`) is for a single arbitrary label per component instance; this needs
// N stable, distinct colors within ONE chart instance instead. `MAX_STATEMENT_LINE_ITEMS` (the
// top-N cutoff below) is capped at this array's length so `STATEMENT_LINE_COLORS[i]` is always
// defined — never derive one from the other, or a future "top 12" change would silently start
// recycling colors here.
const STATEMENT_LINE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
  "var(--chart-9)",
  "var(--chart-10)",
] as const;
const MAX_STATEMENT_LINE_ITEMS = STATEMENT_LINE_COLORS.length;

// The reference design's own 3-4-headline-metric chart (營業毛利/營業淨利/稅後淨利 for 損益表) —
// items in these sets start visible when present in `topItems`; the remaining tracked items
// still list in the legend (never dropped from the chart's data), just start toggled off so 10
// simultaneous lines don't read as visual noise. Each statement type needs its own headline set
// since 損益表/資產負債表/現金流量表 track entirely different line items — labels are this
// dataset's own line-item names, not hand-picked guesses; a label absent from a given quarter's
// `topItems` simply never renders (no-op, not an error) since `topItems` is intersected against
// this set at render time. TW rows use FinMind's own Chinese line-item names
// (`sources/finmind/fundamental.py`); every other market's rows are yfinance-sourced and use
// yfinance's own English item names instead (`sources/yfinance/price_financials.py`'s
// `_financial_statement_lines`) — the two vocabularies share no overlap, so this needs one
// headline set per (statement type, source vocabulary) pair, picked by market in
// `FinancialStatementCard` (`isTw ? ...FinMind : ...Yfinance`).
const STATEMENT_DEFAULT_VISIBLE_ITEMS_FINMIND: Record<
  FinancialStatementType,
  ReadonlySet<string>
> = {
  income_statement: new Set([
    "營業收入",
    "營業毛利（毛損）",
    "營業利益（損失）",
    "本期淨利（淨損）",
  ]),
  balance_sheet: new Set(["資產總額", "負債總額", "權益總額", "現金及約當現金"]),
  cash_flow: new Set([
    "營業活動之淨現金流入（流出）",
    "投資活動之淨現金流入（流出）",
    "本期現金及約當現金增加（減少）數",
  ]),
};
const STATEMENT_DEFAULT_VISIBLE_ITEMS_YFINANCE: Record<
  FinancialStatementType,
  ReadonlySet<string>
> = {
  income_statement: new Set(["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]),
  balance_sheet: new Set([
    "Total Assets",
    "Total Liabilities Net Minority Interest",
    "Stockholders Equity",
    "Cash And Cash Equivalents",
  ]),
  cash_flow: new Set([
    "Operating Cash Flow",
    "Investing Cash Flow",
    "Financing Cash Flow",
    "Free Cash Flow",
  ]),
};

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

/** `get_month_revenue` doesn't return more than a few recent rows when called with no
 * `start_date` — the router/adapter's own default window is narrower than the range toggle
 * needs, so the 6M/1Y control must pass an explicit cutoff rather than slicing a fixed-size
 * response client-side. */
function monthsAgoIso(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

/**
 * 財務分析 tab — 月營收趨勢 (`get_month_revenue`, combo revenue+price chart, ▲▼-tinted YoY badge on
 * the latest month), 股利政策 (`DividendPolicyCard`, backed by the composite
 * `get_dividend_policy_summary` — stats strip, stacked cash+stock chart by year, 年季 table),
 * 財務結構/獲利趨勢 sub-tabs (損益表/資產負債表/現金流, `get_financial_statement` per
 * `statement_type`, multi-quarter line chart of the latest period's top-10 line items). Every
 * card's own richer colored table sits directly below its chart — none wrap in
 * `ChartWithDataSheet`'s raw-rows sheet, which would just duplicate the same data less legibly.
 */
export function FinancialsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  // 月營收 (monthly revenue) is a TW-specific disclosure concept — `get_month_revenue` has no
  // US/non-TW equivalent dataset, so this card is dropped rather than rendered empty for those
  // markets.
  const isTw = market === "TW";
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {isTw ? <MonthRevenueCard symbol={symbol} market={market} /> : null}
      <DividendPolicyCard symbol={symbol} market={market} />
      <FinancialStatementCard symbol={symbol} market={market} />
    </div>
  );
}

const REVENUE_RANGE_MONTHS = { "1y": 12, "3y": 36, "5y": 60 } as const;
type RevenueRange = keyof typeof REVENUE_RANGE_MONTHS;
const REVENUE_RANGE_OPTIONS = Object.keys(REVENUE_RANGE_MONTHS) as RevenueRange[];

// Same collapsed-row-count convention `DIVIDEND_EVENT_COLLAPSED_ROW_COUNT` uses — a handful of
// most-recent months inline, "展開完整" opens the rest in `ExpandModal`.
const MONTH_REVENUE_COLLAPSED_ROW_COUNT = 6;

/** 月營收趨勢 — a combo chart (monthly-revenue bars + period-end stock-price line on a secondary
 * axis, same dual-`yAxisId` `ComposedChart` shape `InstitutionalFlowBody` already uses) plus a
 * 紅漲綠跌-colored 年月/月營收/月增率/年增率/累計年增% table below it, matching the reference TW
 * finance app's own 月營收 view. Replaces the old bare single-series revenue bar chart, which had
 * no price context and no table — every figure (MoM/YoY/cumulative-YoY/period-high/period-end
 * close) is pre-computed server-side in `trend` (`get_month_revenue`'s own docstring), never
 * re-derived client-side from raw rows. */
function MonthRevenueCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.financials");
  const locale = useLocale();
  const [range, setRange] = useState<RevenueRange>("1y");
  const [expanded, setExpanded] = useState(false);
  // Fetch one extra trailing year beyond the displayed range — `yoy_pct`/`cumulative_yoy_pct`
  // need the same calendar month(s) from the prior year to diff against, so a "1y" display
  // window needs 24 months of fetched history or every row's YoY comes back `None` (the prior
  // year's row for the earliest displayed months would simply never have been fetched).
  const fetchStartDate = monthsAgoIso(REVENUE_RANGE_MONTHS[range] + 12);
  const query = useQuery({
    queryKey: ["month_revenue", symbol, market, range],
    queryFn: () => getMonthRevenue(symbol, market, { startDate: fetchStartDate }),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5 xl:col-span-2">
        <CardSkeletonBody height={420} />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const displayStartDate = monthsAgoIso(REVENUE_RANGE_MONTHS[range]);
  const trend = [...(query.data?.trend ?? [])]
    .sort((a, b) => a.revenue_year - b.revenue_year || a.revenue_month - b.revenue_month)
    .filter(
      (point) =>
        `${point.revenue_year}-${String(point.revenue_month).padStart(2, "0")}-01` >=
        displayStartDate,
    );
  const latest = trend.at(-1);
  const latestUp = (latest?.yoy_pct ?? 0) >= 0;

  const points = trend.map((point) => ({
    label: `${point.revenue_year}-${String(point.revenue_month).padStart(2, "0")}`,
    revenue: point.revenue,
    price: point.period_end_close,
  }));
  const rowsDesc = [...trend].reverse();

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("monthRevenueTitle")}</h2>
        <div className="flex items-center gap-2">
          <RangeSelect
            value={range}
            onChange={setRange}
            options={REVENUE_RANGE_OPTIONS}
            labelFor={(key) => t(`monthRevenueRange.${key}`)}
            ariaLabel={t("monthRevenueTitle")}
          />
          {latest?.yoy_pct != null ? (
            <span
              role="group"
              aria-label={`YoY ${latestUp ? "上漲" : "下跌"} ${Math.abs(latest.yoy_pct).toFixed(1)}%`}
              className="font-num text-[13px] tabular-nums"
              style={{ color: latestUp ? "var(--up)" : "var(--down)" }}
            >
              {latestUp ? "▲" : "▼"} {Math.abs(latest.yoy_pct).toFixed(1)}%
            </span>
          ) : null}
        </div>
      </div>
      {points.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <>
          <div style={{ width: "100%", height: 260 }}>
            <ResponsiveContainer>
              <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: "var(--text-muted)", fontSize: 10 }}
                  stroke="var(--border)"
                />
                <YAxis
                  yAxisId="revenue"
                  tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                  tickFormatter={(v: number) => formatCompactNumber(v, locale)}
                  width={48}
                  stroke="var(--border)"
                />
                <YAxis
                  yAxisId="price"
                  orientation="right"
                  tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                  tickFormatter={(v: number) => formatNumber(v, locale)}
                  width={48}
                  stroke="var(--border)"
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "var(--text)" }}
                  itemStyle={{ color: "var(--text)" }}
                  cursor={{ fill: "var(--accent)", opacity: 0.08 }}
                  formatter={(value) => formatNumber(Number(value), locale)}
                />
                <Bar
                  yAxisId="revenue"
                  dataKey="revenue"
                  name={t("monthRevenueLegend")}
                  fill="var(--chart-1)"
                  radius={2}
                />
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="price"
                  name={t("priceLegend")}
                  stroke="var(--chart-5)"
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4">
            <MonthRevenueTable
              rows={rowsDesc.slice(0, MONTH_REVENUE_COLLAPSED_ROW_COUNT)}
              t={t}
              locale={locale}
            />
          </div>

          {rowsDesc.length > MONTH_REVENUE_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className={cn(
                "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              )}
            >
              <span>{t("expandTo", { count: rowsDesc.length })}</span>
            </button>
          ) : null}
        </>
      )}

      <ExpandModal
        open={expanded}
        title={t("monthRevenueTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <MonthRevenueTable rows={rowsDesc} t={t} locale={locale} />
      </ExpandModal>
    </section>
  );
}

function MonthRevenueTable({
  rows,
  t,
  locale,
}: {
  readonly rows: readonly MonthRevenueTrendPoint[];
  readonly t: ReturnType<typeof useTranslations>;
  readonly locale: string;
}): ReactNode {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("monthRevenueTable.period")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("monthRevenueTable.revenue")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("monthRevenueTable.mom")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("monthRevenueTable.yoy")}</th>
            <th className="px-2 py-1.5 text-right font-medium">
              {t("monthRevenueTable.cumulativeYoy")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {rows.map((point) => (
            <tr key={`${point.revenue_year}-${point.revenue_month}`}>
              <td className="px-2 py-1.5 text-[var(--text)]">
                {point.revenue_year}/{String(point.revenue_month).padStart(2, "0")}
              </td>
              <td className="whitespace-nowrap px-1.5 py-1.5 text-right font-num text-[var(--text)] tabular-nums sm:px-2">
                {formatCompactNumber(point.revenue, locale)}
              </td>
              <PctCell value={point.mom_pct} />
              <PctCell value={point.yoy_pct} />
              <PctCell value={point.cumulative_yoy_pct} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One 紅漲綠跌-colored percentage cell — shared by every column in `MonthRevenueTable` so
 * 月增率/年增率/累計年增% never drift on the sign→color/▲▼ mapping. */
function PctCell({ value }: { readonly value: number | null | undefined }): ReactNode {
  if (value == null) {
    return (
      <td className="whitespace-nowrap px-1.5 py-1.5 text-right font-num text-[var(--text-muted)] tabular-nums sm:px-2">
        —
      </td>
    );
  }
  return (
    <td
      className="whitespace-nowrap px-1.5 py-1.5 text-right font-num tabular-nums sm:px-2"
      style={{ color: directionColor(value) }}
    >
      {directionGlyph(value)} {Math.abs(value * 100).toFixed(1)}%
    </td>
  );
}

// Same ~3.5-year lookback the reference design's own 8-quarter window needs (quarterly filings,
// so 8 quarters back is ~24 months; a little slack for a quarter landing just before the cutoff).
const STATEMENT_LOOKBACK_MONTHS = 30;
// Same collapsed-row-count convention `MONTH_REVENUE_COLLAPSED_ROW_COUNT` uses — a handful of
// most-recent quarters as chart+table columns inline, "展開完整" opens the rest in `ExpandModal`.
const STATEMENT_COLLAPSED_PERIOD_COUNT = 8;

/** `Legend`'s `formatter` for the 財務報表 line-item click-to-toggle legend — a named function
 * rather than an inline arrow so it isn't redefined on every render of `FinancialStatementCard`. */
function statementLegendFormatter(
  value: string,
  isItemVisible: (item: string) => boolean,
): ReactNode {
  return (
    <span style={{ cursor: "pointer", opacity: isItemVisible(value) ? 1 : 0.4 }}>{value}</span>
  );
}

/** 財務結構/獲利趨勢 sub-tabs — a multi-quarter line chart (top-10 line items by the latest
 * period's |value|, one categorical color per item, legend click-to-toggle same as
 * `DividendPolicyCard`'s chart) plus a 項目 × 季別 grid table below, matching the reference app's
 * own 損益表 trend view but keeping all 10 items this app already surfaces (the reference only
 * tracks 3). The top-10 set is fixed from the latest period so the same 10 lines track across
 * every quarter — picking each quarter's own top-10 independently would let items drop in and
 * out of the chart as their rank shifts, producing broken/discontinuous lines. */
function FinancialStatementCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.financials");
  const locale = useLocale();
  const [statementType, setStatementType] = useState<FinancialStatementType>("income_statement");
  const [periodType, setPeriodType] = useState<PeriodType>("quarterly");
  const [expanded, setExpanded] = useState(false);
  // Tracks which items have been clicked away from their *default* visibility (the market-
  // appropriate `STATEMENT_DEFAULT_VISIBLE_ITEMS_*` set) — never the visibility set directly,
  // since `topItems` isn't known until the query resolves; XORing a toggle set against the fixed
  // default means no effect/derived-state dance is needed to seed it once data arrives.
  const [toggledItems, setToggledItems] = useState<ReadonlySet<string>>(new Set());
  const defaultVisibleItems =
    market === "TW"
      ? STATEMENT_DEFAULT_VISIBLE_ITEMS_FINMIND
      : STATEMENT_DEFAULT_VISIBLE_ITEMS_YFINANCE;
  const isItemVisible = (item: string): boolean =>
    defaultVisibleItems[statementType].has(item) !== toggledItems.has(item);
  const startDate = monthsAgoIso(STATEMENT_LOOKBACK_MONTHS);
  const query = useQuery({
    queryKey: ["financial_statement", symbol, market, statementType, startDate],
    queryFn: () => getFinancialStatement(symbol, market, statementType, { startDate }),
  });

  // `balance_sheet` reports each line twice under the same `origin_name` — the absolute value
  // (`item`, e.g. "TotalAssets") and a same-period percent-of-total (`item + "_per"`, e.g.
  // "TotalAssets_per") — both sharing one `origin_name` (`sources/finmind/fundamental.py`'s own
  // `_per`-suffix convention). `income_statement`/`cash_flow` never emit this suffix, so this
  // filter is a no-op for those, but is load-bearing for `balance_sheet`: without it, the two
  // same-named rows collide in `valueByItemAndPeriod` below (last-write-wins), silently
  // swapping some periods' plotted value for that item between the real figure and a 0-100
  // percentage.
  const allLines = (query.data?.lines ?? []).filter((l) => !l.item.endsWith("_per"));
  // Every FinMind (TW) row is implicitly `period_type="quarterly"` (its only frequency) — the
  // toggle only has real effect for yfinance-sourced (non-TW) rows, which genuinely carry
  // annual/quarterly/ttm as distinct frames (see `FinancialStatementLine`'s own docstring).
  const periodTypes = [...new Set(allLines.map((l) => l.period_type))];
  const lines = allLines.filter((l) => l.period_type === periodType);
  const periods = [...new Set(lines.map((l) => l.period_end))].sort((a, b) => a.localeCompare(b));
  const latestPeriod = periods.at(-1);
  const topItems = [...lines]
    .filter((l) => l.period_end === latestPeriod)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, MAX_STATEMENT_LINE_ITEMS)
    .map((l) => l.origin_name);

  const valueByItemAndPeriod = new Map<string, Map<string, number>>();
  for (const line of lines) {
    if (!topItems.includes(line.origin_name)) continue;
    const byPeriod = valueByItemAndPeriod.get(line.origin_name) ?? new Map<string, number>();
    byPeriod.set(line.period_end, line.value);
    valueByItemAndPeriod.set(line.origin_name, byPeriod);
  }

  const chartPeriods = periods.slice(-STATEMENT_COLLAPSED_PERIOD_COUNT);
  const points = chartPeriods.map((period) => {
    const point: Record<string, string | number> = { period };
    for (const item of topItems) {
      const value = valueByItemAndPeriod.get(item)?.get(period);
      if (value != null) point[item] = value;
    }
    return point;
  });

  let statementBody: ReactNode;
  if (query.isLoading) {
    statementBody = <CardSkeletonBody height={280} />;
  } else if (topItems.length === 0) {
    statementBody = <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  } else {
    statementBody = (
      <>
        <div style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="period"
                tick={{ fill: "var(--text-muted)", fontSize: 10 }}
                stroke="var(--border)"
              />
              <YAxis
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                tickFormatter={(v: number) => formatCompactNumber(v, locale)}
                width={48}
                stroke="var(--border)"
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ color: "var(--text)" }}
                itemStyle={{ color: "var(--text)" }}
                cursor={{ fill: "var(--accent)", opacity: 0.08 }}
                formatter={(value) => formatNumber(Number(value), locale)}
              />
              <Legend
                onClick={(entry) => {
                  const item = entry.dataKey as string;
                  setToggledItems((prev) => {
                    const next = new Set(prev);
                    if (next.has(item)) {
                      next.delete(item);
                    } else {
                      next.add(item);
                    }
                    return next;
                  });
                }}
                formatter={(value) => statementLegendFormatter(value, isItemVisible)}
                wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
              />
              {/* Grouped bars, one per item per quarter: statement figures are discrete period
                  amounts, and side-by-side bars compare a quarter's items (and a loss below zero)
                  more clearly than smoothed lines that imply values between quarters. */}
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              {topItems.map((item, i) => (
                <Bar
                  key={item}
                  dataKey={item}
                  name={item}
                  fill={STATEMENT_LINE_COLORS[i % STATEMENT_LINE_COLORS.length]}
                  radius={[3, 3, 0, 0]}
                  maxBarSize={18}
                  hide={!isItemVisible(item)}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        <div className="mt-4">
          <FinancialStatementTable
            items={topItems.slice(0, PREVIEW_ROWS)}
            periods={chartPeriods}
            valueByItemAndPeriod={valueByItemAndPeriod}
            locale={locale}
            t={t}
          />
        </div>

        {periods.length > STATEMENT_COLLAPSED_PERIOD_COUNT || topItems.length > PREVIEW_ROWS ? (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className={cn(
              "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
              "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
            )}
          >
            <span>{t("expandTo", { count: periods.length })}</span>
          </button>
        ) : null}
      </>
    );
  }

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("statementTitle")}</h2>
        {latestPeriod ? (
          <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
            {latestPeriod}
          </span>
        ) : null}
      </div>
      <div role="tablist" aria-label={t("statementTitle")} className="flex flex-wrap gap-1.5">
        {FINANCIAL_STATEMENT_TYPES.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={statementType === key}
            onClick={() => setStatementType(key)}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              statementType === key
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`statementType.${key}`)}
          </button>
        ))}
      </div>

      {periodTypes.length > 1 ? (
        <div
          role="tablist"
          aria-label={t("periodType.label")}
          className="mt-2 flex flex-wrap gap-1.5"
        >
          {PERIOD_TYPES.filter((pt) => periodTypes.includes(pt)).map((pt) => (
            <button
              key={pt}
              type="button"
              role="tab"
              aria-selected={periodType === pt}
              onClick={() => setPeriodType(pt)}
              className={cn(
                "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11.5px] transition-colors",
                periodType === pt
                  ? "bg-[var(--surface-3)] font-semibold text-[var(--text)]"
                  : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(`periodType.${pt}`)}
            </button>
          ))}
        </div>
      ) : null}

      <div className="mt-3">{statementBody}</div>

      <ExpandModal
        open={expanded}
        title={t("statementTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <FinancialStatementTable
          items={topItems}
          periods={periods}
          valueByItemAndPeriod={valueByItemAndPeriod}
          locale={locale}
          t={t}
        />
      </ExpandModal>
    </section>
  );
}

/** 項目 × 季別 grid — line items as rows, periods as columns, so all 10 tracked items and every
 * fetched quarter are visible without a 10-column-per-quarter table (a quarter-per-row layout
 * would need one column per line item instead, same information, worse fit for 10 items). */
function FinancialStatementTable({
  items,
  periods,
  valueByItemAndPeriod,
  locale,
  t,
}: {
  readonly items: readonly string[];
  readonly periods: readonly string[];
  readonly valueByItemAndPeriod: ReadonlyMap<string, ReadonlyMap<string, number>>;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("statementTable.item")}</th>
            {periods.map((period) => (
              <th key={period} className="px-2 py-1.5 text-right font-medium">
                {period}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {items.map((item, i) => (
            <tr key={item}>
              <td
                className="px-2 py-1.5"
                style={{ color: STATEMENT_LINE_COLORS[i % STATEMENT_LINE_COLORS.length] }}
              >
                {item}
              </td>
              {periods.map((period) => {
                const value = valueByItemAndPeriod.get(item)?.get(period);
                return (
                  <td
                    key={period}
                    className="whitespace-nowrap px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums"
                  >
                    {value != null ? formatCompactNumber(value, locale) : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
