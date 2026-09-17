"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import { Link } from "@/i18n/navigation";
import { getPriceHistory } from "@/lib/api/market";
import {
  getForeignShareholding,
  getGovernmentBankFlows,
  getSecuritiesLending,
  getStockChipFlows,
  getStockDayTrading,
  getStockInstitutionalFlow,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";
import type { DayTradingRow, MarginShortRow } from "@/types/stock-detail";

const INSTITUTIONAL_SUB_TABS = ["flow", "holdings", "summary"] as const;
type InstitutionalSubTab = (typeof INSTITUTIONAL_SUB_TABS)[number];

const MARGIN_SUB_TABS = ["margin", "short", "ratio", "dayTrading", "lending"] as const;
type MarginSubTab = (typeof MARGIN_SUB_TABS)[number];

const ROLLING_NET_WINDOWS = ["5d", "10d", "20d"] as const;
type RollingNetWindow = (typeof ROLLING_NET_WINDOWS)[number];

/** 分點's 買賣超 tab period selector — days back from today, mapped to `getStockChipFlows`'s
 * `start_date` param. `get_chip_flows`'s upstream (`TaiwanStockTradingDailyReport`) is
 * single-trading-day-per-call, so the tool fans out to one call per day and merges — bounded
 * server-side to `_CHIP_BROKER_MAX_TRADING_DAYS` (10, see `market_tools.py`) so one page load
 * never issues more than 10 sequential Sponsor-tier FinMind calls. This selector's own widest
 * option (10日) matches that cap exactly — a wider window here would silently be truncated
 * server-side to the same 10 days anyway, so the UI never offers one. The reference app's own
 * 一月/一季/半年/一年 options and its 排行 (price-correlation) tab both need far more days of
 * history than this cap allows for a meaningful result, so neither is built here — see this
 * panel's own PR notes for why a 5-10-point correlation was judged not worth presenting as real
 * analysis. `recent` mirrors the reference app's own default "近日" (latest trading day only). */
const BROKER_LOOKBACK_WINDOWS = ["recent", "5d", "10d"] as const;
type BrokerLookbackWindow = (typeof BROKER_LOOKBACK_WINDOWS)[number];

const BROKER_LOOKBACK_DAYS: Record<BrokerLookbackWindow, number> = {
  recent: 1,
  "5d": 5,
  "10d": 10,
};

const BROKER_TOP_DIRECTIONS = ["buy", "sell"] as const;
type BrokerTopDirection = (typeof BROKER_TOP_DIRECTIONS)[number];

const TOP_BROKER_ROW_COUNT = 15;

function isoDaysAgo(days: number): string {
  const ms = Date.now() - days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

/** `investor` category colors for 摘要's 法人籌碼 chart — categorical `--chart-1..6` tokens, one
 * per category (`get_institutional_flow` gives 5 real categories), plus a 6th slot
 * (`GOVERNMENT_BANK_INVESTOR_KEY`) for the Sponsor-tier 八大行庫 series added below, never
 * `--up`/`--down`: those are reserved for signed price/%/score deltas, not a category axis
 * (web/CLAUDE.md §4). */
const INVESTOR_CATEGORY_COLORS: Record<string, string> = {
  Foreign_Investor: "var(--chart-1)",
  Investment_Trust: "var(--chart-2)",
  Dealer_self: "var(--chart-3)",
  Dealer_Hedging: "var(--chart-4)",
  Foreign_Dealer_Self: "var(--chart-5)",
  GovernmentBank: "var(--chart-8)",
};
// Not a real FinMind `investor` code (those all come from `get_institutional_flow`) — a
// synthetic key this panel invents so 八大行庫's own bar can share the same `INVESTOR_CATEGORY_
// COLORS`/`investorLabel` plumbing as the 5 real categories, rather than a parallel code path.
const GOVERNMENT_BANK_INVESTOR_KEY = "GovernmentBank";

// The reference app's own 三大法人 grouping — `get_institutional_flow`'s 5 real FinMind
// categories collapsed into the 3 buckets every TW retail chip-analysis view uses: 外資
// (Foreign_Investor + Foreign_Dealer_Self, both foreign-desk activity), 投信 (Investment_Trust
// alone), 自營商 (Dealer_self + Dealer_Hedging, self-trading + hedging books). Order here is the
// display order for every 三大法人 chart/table/stat in this panel.
const INSTITUTIONAL_BUCKETS = ["foreign", "trust", "dealer"] as const;
type InstitutionalBucket = (typeof INSTITUTIONAL_BUCKETS)[number];
const BUCKET_BY_INVESTOR: Record<string, InstitutionalBucket> = {
  Foreign_Investor: "foreign",
  Foreign_Dealer_Self: "foreign",
  Investment_Trust: "trust",
  Dealer_self: "dealer",
  Dealer_Hedging: "dealer",
};
const BUCKET_COLORS: Record<InstitutionalBucket, string> = {
  foreign: "var(--chart-1)",
  trust: "var(--chart-2)",
  dealer: "var(--chart-3)",
};

function bucketLabel(t: ReturnType<typeof useTranslations>, bucket: InstitutionalBucket): string {
  return t(`bucket.${bucket}`);
}

/** Sums `get_institutional_flow`'s 5 raw categories into the 3 display buckets, keyed by
 * `trade_date` — the one grouping pass every 三大法人 chart/table in this panel shares, so the
 * bucket totals a chart plots and a table prints can never drift apart from double
 * implementation. */
function bucketedFlowByDate(
  rows: readonly { trade_date: string; investor: string; buy: number; sell: number }[],
): Map<string, Record<InstitutionalBucket, number>> {
  const byDate = new Map<string, Record<InstitutionalBucket, number>>();
  for (const row of rows) {
    const bucket = BUCKET_BY_INVESTOR[row.investor];
    if (!bucket) continue;
    const entry = byDate.get(row.trade_date) ?? { foreign: 0, trust: 0, dealer: 0 };
    entry[bucket] += row.buy - row.sell;
    byDate.set(row.trade_date, entry);
  }
  return byDate;
}

/** `fieldLabel` resolves through the shared `chat.cards.fields` namespace (not this panel's own
 * `stock.chipAnalysis` one), and its `LABELED_FIELDS` map is keyed lowercase (`foreign_investor`)
 * while the raw `investor` row value arrives PascalCase (`Foreign_Investor`) — lowercase before
 * lookup, same pattern `institutional-flow-timeseries.tsx`'s own `categoryLabel` helper uses. */
function investorLabel(tFields: ReturnType<typeof useTranslations>, investor: string): string {
  return fieldLabel(tFields, investor.toLowerCase());
}

/** 摘要's 法人籌碼 chart X-axis tick — colors each category's own label to match its bar's fill
 * (`INVESTOR_CATEGORY_COLORS`), rather than every label sharing one flat `--text-muted` — a live
 * screenshot flagged the flat-gray labels as illegible next to their colored bars. Recharts
 * passes tick props (`x`/`y`/`payload`) positionally, so this has to be a custom SVG `<text>`,
 * not a plain `tickFormatter` string (that API has no way to set per-tick color). */
function InvestorTick(props: {
  readonly x?: number;
  readonly y?: number;
  readonly payload?: { value: string };
  readonly tFields: ReturnType<typeof useTranslations>;
  readonly colors: Record<string, string>;
}): ReactNode {
  const { x = 0, y = 0, payload, tFields, colors } = props;
  const investor = payload?.value ?? "";
  return (
    <text
      x={x}
      y={y}
      dy={12}
      textAnchor="middle"
      fontSize={10}
      fill={colors[investor] ?? "var(--text-muted)"}
    >
      {investorLabel(tFields, investor)}
    </text>
  );
}

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

/**
 * 籌碼分析 tab — 4 permanent cards, 2 stacked 2-column rows (no top-level pill switcher —
 * every card renders at once, per explicit direction): 摘要 (`get_institutional_flow`'s own
 * `rolling_net`, no distinct "summary" capability exists — confirmed against the backend, so
 * this is a re-presentation, not a new fetch) + 分點 (`get_chip_flows`, Sponsor-tier →
 * `SponsorTeaser`) on top; 三大法人 (進出/持股 sub-tabs) + 融資券 (融資/融券/券資比/當沖/借券
 * sub-tabs) below. 借券 folded into 融資券's own sub-tab row rather than dropped, since its data
 * is real and already wired — only the standalone top-level switcher was removed.
 */
export function ChipAnalysisPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-2">
        <SummaryColumn symbol={symbol} market={market} />
        <BrokersColumn symbol={symbol} market={market} />
      </div>
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-2">
        <InstitutionalColumn symbol={symbol} market={market} />
        <MarginShortColumn symbol={symbol} market={market} />
      </div>
    </div>
  );
}

// Shared row min-heights — 摘要/分點 and 三大法人/融資券 are each two fully independent cards
// with their OWN sub-tab switcher, so their real content height varies with whatever sub-tab
// that card currently has selected (a compact stat block vs. a full chart). Without a shared
// floor the two cards in a row visibly mismatch height whenever their sub-tabs land on very
// different content shapes — confirmed live via screenshot (三大法人's 摘要 stat block at 217px
// next to 融資券's 借券 chart at 459px). Sized to each row's tallest realistic chart-shaped
// sub-tab (observed live); a sub-tab whose own content exceeds this still grows the card
// normally — this is a floor, never a clamp.
const SUMMARY_ROW_MIN_HEIGHT = "min-h-[560px]";
const INSTITUTIONAL_ROW_MIN_HEIGHT = "min-h-[460px]";

function SummaryColumn({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <section
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        SUMMARY_ROW_MIN_HEIGHT,
      )}
    >
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("column.summary")}</h2>
      <h3 className="mb-2 font-medium text-[13px] text-[var(--text-muted)]">
        {t("summarySection.institutional")}
      </h3>
      <InstitutionalSummaryBody symbol={symbol} market={market} />
      <h3 className="mt-5 mb-2 font-medium text-[13px] text-[var(--text-muted)]">
        {t("summarySection.marginShort")}
      </h3>
      <MarginShortSnapshot symbol={symbol} market={market} />
    </section>
  );
}

function BrokersColumn({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <section
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        SUMMARY_ROW_MIN_HEIGHT,
      )}
    >
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("column.brokers")}</h2>
      <ChipFlowsBody symbol={symbol} market={market} />
    </section>
  );
}

function SubTabRow<T extends string>({
  tabs,
  active,
  onChange,
  labelFor,
}: {
  readonly tabs: readonly T[];
  readonly active: T;
  readonly onChange: (tab: T) => void;
  readonly labelFor: (tab: T) => string;
}): ReactNode {
  return (
    <div className="mb-3 flex flex-wrap gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
      {tabs.map((tab) => (
        <button
          key={tab}
          type="button"
          onClick={() => onChange(tab)}
          aria-pressed={active === tab}
          className={cn(
            "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
            active === tab
              ? "bg-[var(--accent)] text-[color:#06231F]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {labelFor(tab)}
        </button>
      ))}
    </div>
  );
}

function InstitutionalColumn({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const [subTab, setSubTab] = useState<InstitutionalSubTab>("flow");

  return (
    <section
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        INSTITUTIONAL_ROW_MIN_HEIGHT,
      )}
    >
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">
        {t("column.institutional")}
      </h2>
      <SubTabRow
        tabs={INSTITUTIONAL_SUB_TABS}
        active={subTab}
        onChange={setSubTab}
        labelFor={(tab) => t(`institutionalSubTab.${tab}`)}
      />
      {subTab === "flow" ? <InstitutionalFlowBody symbol={symbol} market={market} /> : null}
      {subTab === "holdings" ? <ForeignShareholdingBody symbol={symbol} market={market} /> : null}
      {subTab === "summary" ? <InstitutionalTotalsBody symbol={symbol} market={market} /> : null}
    </section>
  );
}

function MarginShortColumn({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const [subTab, setSubTab] = useState<MarginSubTab>("margin");

  return (
    <section
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        INSTITUTIONAL_ROW_MIN_HEIGHT,
      )}
    >
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">
        {t("column.marginShort")}
      </h2>
      <SubTabRow
        tabs={MARGIN_SUB_TABS}
        active={subTab}
        onChange={setSubTab}
        labelFor={(tab) => t(`marginSubTab.${tab}`)}
      />
      <MarginShortBody symbol={symbol} market={market} view={subTab} />
    </section>
  );
}

const INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS = 30; // matches get_institutional_flow's own
// default lookback window (no start_date passed here) so the price-overlay series covers the
// same date range as the flow bars, never a longer/shorter window that would silently misalign.

// Same collapsed-row-count convention every other table in this codebase uses — a handful of
// most-recent days inline, "展開完整" opens the rest in `ExpandModal`.
const INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT = 6;

/** 進出 chart's click-to-toggle legend (3 buckets + price) — a named module-level component
 * rather than an inline closure passed to `Legend`'s `content` prop, so it isn't redefined on
 * every render of its parent. */
function FlowLegend({
  hiddenSeries,
  toggleSeries,
  t,
  tFields,
}: {
  readonly hiddenSeries: ReadonlySet<string>;
  readonly toggleSeries: (series: string) => void;
  readonly t: ReturnType<typeof useTranslations>;
  readonly tFields: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <ul className="mt-2 flex flex-wrap justify-center gap-3">
      {[...INSTITUTIONAL_BUCKETS, "price"].map((series) => {
        const isHidden = hiddenSeries.has(series);
        const color =
          series === "price" ? "var(--chart-9)" : BUCKET_COLORS[series as InstitutionalBucket];
        const label =
          series === "price"
            ? tFields("fields.close")
            : bucketLabel(t, series as InstitutionalBucket);
        return (
          <li key={series}>
            <button
              type="button"
              onClick={() => toggleSeries(series)}
              className="inline-flex items-center gap-1.5 text-[11px]"
              style={{ color: isHidden ? "var(--text-muted)" : "var(--text)" }}
            >
              <span
                aria-hidden
                className="inline-block size-2.5 rounded-sm"
                style={{ backgroundColor: isHidden ? "var(--text-muted)" : color }}
              />
              {label}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** 三大法人's 進出 chart — 3 separately-colored bars per day (外資/投信/自營商 net, bucketed from
 * `get_institutional_flow`'s 5 real categories via `bucketedFlowByDate`) with a price-line
 * overlay on a secondary axis, plus a 紅漲綠跌-colored 日期/外資/投信/自營商/合計 table below —
 * matching the reference app's own 進出 view exactly (chart + table, not chart alone). Price
 * comes from `getPriceHistory` (already used in `AssetHeader`), merged by `trade_date` — a day
 * with a flow row but no matching price bar (a gap either series might have) simply renders
 * `price: null`, which recharts skips as a broken line segment rather than a fabricated zero. */
function InstitutionalFlowBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  // Clicking a legend entry toggles that series off/on — same interactive-legend pattern
  // `InstitutionalFlowCard` (market/institutional-flow-card.tsx) already uses, applied here to
  // both the 3 bucket bars and the price line.
  const [hiddenSeries, setHiddenSeries] = useState<ReadonlySet<string>>(new Set());
  const toggleSeries = (series: string): void => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(series)) {
        next.delete(series);
      } else {
        next.add(series);
      }
      return next;
    });
  };
  const flowQuery = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const priceQuery = useQuery({
    queryKey: ["price_history", symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS],
    queryFn: () => getPriceHistory([symbol], market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS),
  });

  if (flowQuery.isLoading) return <CardSkeletonBody height={220} />;
  const rows = flowQuery.data?.rows ?? [];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const byDate = bucketedFlowByDate(rows);
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  const datesAsc = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  const points = datesAsc.map((trade_date) => {
    const bucket = byDate.get(trade_date);
    return {
      trade_date,
      price: priceByDate.get(trade_date) ?? null,
      foreign: bucket?.foreign ?? 0,
      trust: bucket?.trust ?? 0,
      dealer: bucket?.dealer ?? 0,
    };
  });
  const tableRowsDesc = [...datesAsc].reverse();

  return (
    <>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="flow"
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
            <ReferenceLine yAxisId="flow" y={0} stroke="var(--border)" />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(value, name) => [
                formatNumber(Number(value), locale),
                name === "price"
                  ? tFields("fields.close")
                  : bucketLabel(t, name as InstitutionalBucket),
              ]}
              labelFormatter={(label) => String(label)}
            />
            <Legend
              content={() => (
                <FlowLegend
                  hiddenSeries={hiddenSeries}
                  toggleSeries={toggleSeries}
                  t={t}
                  tFields={tFields}
                />
              )}
            />
            {INSTITUTIONAL_BUCKETS.map((bucket) => (
              <Bar
                key={bucket}
                yAxisId="flow"
                dataKey={bucket}
                name={bucket}
                stackId="flow"
                fill={BUCKET_COLORS[bucket]}
                radius={2}
                hide={hiddenSeries.has(bucket)}
              />
            ))}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name="price"
              stroke="var(--chart-9)"
              strokeWidth={2}
              dot={false}
              connectNulls
              hide={hiddenSeries.has("price")}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4">
        <InstitutionalFlowTable
          dates={tableRowsDesc.slice(0, INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT)}
          byDate={byDate}
          locale={locale}
        />
      </div>

      {tableRowsDesc.length > INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: tableRowsDesc.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("institutionalSubTab.flow")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <InstitutionalFlowTable dates={tableRowsDesc} byDate={byDate} locale={locale} />
      </ExpandModal>
    </>
  );
}

/** 進出's 日期/外資/投信/自營商/合計 table — 紅漲綠跌-colored per cell (TW convention: net buy
 * red/▲, net sell green/▼), most-recent-first. Shares `byDate`'s bucket totals with the chart
 * above so the two views can never disagree on a given day's numbers. */
function InstitutionalFlowTable({
  dates,
  byDate,
  locale,
}: {
  readonly dates: readonly string[];
  readonly byDate: ReadonlyMap<string, Record<InstitutionalBucket, number>>;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("flowTable.date")}</th>
            {INSTITUTIONAL_BUCKETS.map((bucket) => (
              <th key={bucket} className="px-2 py-1.5 text-right font-medium">
                {bucketLabel(t, bucket)}
              </th>
            ))}
            <th className="px-2 py-1.5 text-right font-medium">{t("flowTable.total")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {dates.map((date) => {
            const bucket = byDate.get(date) ?? { foreign: 0, trust: 0, dealer: 0 };
            const total = bucket.foreign + bucket.trust + bucket.dealer;
            return (
              <tr key={date}>
                <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{date}</td>
                <NetFlowCell value={bucket.foreign} locale={locale} />
                <NetFlowCell value={bucket.trust} locale={locale} />
                <NetFlowCell value={bucket.dealer} locale={locale} />
                <NetFlowCell value={total} locale={locale} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** One 紅漲綠跌-colored net-flow cell (▲ red for net buy, ▼ green for net sell) — shared by every
 * 三大法人 table in this panel so the sign→color/▲▼ mapping never drifts between them. */
function NetFlowCell({
  value,
  locale,
}: {
  readonly value: number;
  readonly locale: string;
}): ReactNode {
  const up = value >= 0;
  return (
    <td
      className="px-2 py-1.5 text-right font-num tabular-nums"
      style={{ color: up ? "var(--up)" : "var(--down)" }}
    >
      {up ? "▲" : "▼"} {formatNumber(Math.abs(value), locale)}
    </td>
  );
}

const SUMMARY_PERIODS = ["day", "week", "month"] as const;
type SummaryPeriod = (typeof SUMMARY_PERIODS)[number];
// Trading-day counts each period's mini-chart/stat-block looks back over — matches the
// reference app's own 日/週/月 tabs (one day, one calendar week ≈5 trading days, one calendar
// month ≈20 trading days) rather than a literal single day/week/month window, since the flow
// data this reads is trading-day-indexed, not calendar-indexed.
const SUMMARY_PERIOD_TRADING_DAYS: Record<SummaryPeriod, number> = { day: 1, week: 5, month: 20 };

/** 三大法人's 摘要 sub-tab — a top totals row (三大法人 combined + 外資/投信/自營商, each 累計
 * 買賣超 + estimated %-of-shares-outstanding, each card tinted by its own bucket color) plus
 * 日/週/月 tabs, each showing that period's net-flow-per-bucket stat block and a mini combo
 * chart (bucket net-flow bars + price line) over the period's trading-day window. 外資's %
 * comes from `get_foreign_shareholding`'s real ratio; 投信/自營商 have no per-stock
 * holding-balance disclosure anywhere in this codebase's configured sources (confirmed:
 * neither FinMind, TWSE OpenAPI, nor TPEx OpenAPI publishes one — see `ForeignShareholdingBody`'s
 * own docstring for the full investigation), so their %s are the same cumulative-net-flow
 * estimate that component uses. */
function InstitutionalTotalsBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [period, setPeriod] = useState<SummaryPeriod>("day");
  const [hiddenBuckets, setHiddenBuckets] = useState<ReadonlySet<InstitutionalBucket>>(new Set());
  const toggleBucket = (bucket: InstitutionalBucket): void => {
    setHiddenBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(bucket)) {
        next.delete(bucket);
      } else {
        next.add(bucket);
      }
      return next;
    });
  };
  const flowQuery = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const shareholdingQuery = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market],
    queryFn: () => getForeignShareholding(symbol, market),
  });
  const priceQuery = useQuery({
    queryKey: ["price_history", symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS],
    queryFn: () => getPriceHistory([symbol], market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS),
  });

  if (flowQuery.isLoading || shareholdingQuery.isLoading) return <CardSkeletonBody height={320} />;
  const flowRows = flowQuery.data?.rows ?? [];
  if (flowRows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const byDate = bucketedFlowByDate(flowRows);
  const datesAsc = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  const cumulativeTotals = datesAsc.reduce(
    (acc, date) => {
      const bucket = byDate.get(date);
      if (bucket) {
        acc.foreign += bucket.foreign;
        acc.trust += bucket.trust;
        acc.dealer += bucket.dealer;
      }
      return acc;
    },
    { foreign: 0, trust: 0, dealer: 0 },
  );
  const combinedTotal = cumulativeTotals.foreign + cumulativeTotals.trust + cumulativeTotals.dealer;

  const shareholdingRows = [...(shareholdingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestShareholding = shareholdingRows.at(-1);
  const sharesIssued = latestShareholding?.number_of_shares_issued;
  const bucketPct = (shares: number): number | null =>
    sharesIssued && sharesIssued > 0 ? (shares / sharesIssued) * 100 : null;

  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }
  const windowDates = datesAsc.slice(-SUMMARY_PERIOD_TRADING_DAYS[period]);
  const periodTotals = windowDates.reduce(
    (acc, date) => {
      const bucket = byDate.get(date);
      if (bucket) {
        acc.foreign += bucket.foreign;
        acc.trust += bucket.trust;
        acc.dealer += bucket.dealer;
      }
      return acc;
    },
    { foreign: 0, trust: 0, dealer: 0 },
  );
  const periodPoints = windowDates.map((trade_date) => {
    const bucket = byDate.get(trade_date);
    return {
      trade_date,
      foreign: bucket?.foreign ?? 0,
      trust: bucket?.trust ?? 0,
      dealer: bucket?.dealer ?? 0,
      price: priceByDate.get(trade_date) ?? null,
    };
  });

  const totalsCards: {
    key: string;
    label: string;
    shares: number;
    pct: number | null;
    color: string;
  }[] = [
    {
      key: "combined",
      label: t("institutionalCombined"),
      shares: combinedTotal,
      pct: bucketPct(combinedTotal),
      color: "var(--accent)",
    },
    {
      key: "foreign",
      label: bucketLabel(t, "foreign"),
      shares: cumulativeTotals.foreign,
      pct:
        latestShareholding?.foreign_investment_shares_ratio ?? bucketPct(cumulativeTotals.foreign),
      color: BUCKET_COLORS.foreign,
    },
    {
      key: "trust",
      label: bucketLabel(t, "trust"),
      shares: cumulativeTotals.trust,
      pct: bucketPct(cumulativeTotals.trust),
      color: BUCKET_COLORS.trust,
    },
    {
      key: "dealer",
      label: bucketLabel(t, "dealer"),
      shares: cumulativeTotals.dealer,
      pct: bucketPct(cumulativeTotals.dealer),
      color: BUCKET_COLORS.dealer,
    },
  ];

  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {totalsCards.map((card) => {
          // "combined" has no matching bucket bar in the chart below (it's the sum of all 3),
          // so it's rendered as plain, non-interactive stat card — only the 3 per-bucket cards
          // toggle a chart series, same click-to-hide pattern the 進出 chart's own `<Legend>` uses.
          const bucket = INSTITUTIONAL_BUCKETS.find((b) => b === card.key);
          const isHidden = bucket ? hiddenBuckets.has(bucket) : false;
          const content = (
            <>
              <p className="text-[11px] text-[var(--text-muted)]">{card.label}</p>
              <p
                className="font-num text-[14px] tabular-nums"
                style={{ color: isHidden ? "var(--text-muted)" : card.color }}
              >
                {formatCompactNumber(card.shares, locale)}
              </p>
              <p className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                {card.pct != null ? `佔${card.pct.toFixed(2)}%` : "—"}
              </p>
            </>
          );
          const style = {
            borderColor: `color-mix(in srgb, ${card.color} ${isHidden ? 15 : 35}%, transparent)`,
            backgroundColor: `color-mix(in srgb, ${card.color} ${isHidden ? 5 : 14}%, transparent)`,
          };
          if (!bucket) {
            return (
              <div
                key={card.key}
                className="rounded-[var(--radius-input)] border p-2.5 backdrop-blur-md"
                style={style}
              >
                {content}
              </div>
            );
          }
          return (
            <button
              key={card.key}
              type="button"
              onClick={() => toggleBucket(bucket)}
              aria-pressed={!isHidden}
              className="rounded-[var(--radius-input)] border p-2.5 text-left backdrop-blur-md transition-opacity"
              style={style}
            >
              {content}
            </button>
          );
        })}
      </div>

      <SubTabRow
        tabs={SUMMARY_PERIODS}
        active={period}
        onChange={setPeriod}
        labelFor={(p) => t(`summaryPeriod.${p}`)}
      />
      <div className="mb-3 grid grid-cols-3 gap-2 font-num text-[12px] tabular-nums">
        {INSTITUTIONAL_BUCKETS.map((bucket) => (
          <SnapshotStat
            key={bucket}
            label={bucketLabel(t, bucket)}
            value={formatSignedNumber(periodTotals[bucket], locale)}
            tone={periodTotals[bucket] >= 0 ? "up" : "down"}
          />
        ))}
      </div>
      <div style={{ width: "100%", height: 160 }}>
        <ResponsiveContainer>
          <ComposedChart data={periodPoints} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 9 }}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="flow"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickFormatter={(v: number) => formatCompactNumber(v, locale)}
              width={40}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="price"
              orientation="right"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickFormatter={(v: number) => formatNumber(v, locale)}
              width={40}
              stroke="var(--border)"
            />
            <ReferenceLine yAxisId="flow" y={0} stroke="var(--border)" />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(value, name) => [
                formatNumber(Number(value), locale),
                name === "price"
                  ? tFields("fields.close")
                  : bucketLabel(t, name as InstitutionalBucket),
              ]}
            />
            {INSTITUTIONAL_BUCKETS.map((bucket) => (
              <Bar
                key={bucket}
                yAxisId="flow"
                dataKey={bucket}
                name={bucket}
                stackId="flow"
                fill={BUCKET_COLORS[bucket]}
                radius={2}
                hide={hiddenBuckets.has(bucket)}
              />
            ))}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name="price"
              stroke="var(--chart-9)"
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** 摘要's 法人籌碼 chart — reference app's own 摘要表 shows one bar chart of net buy/sell per
 * investor category, with a lookback-window selector (5日/10日/20日), not a flat number list.
 * `get_institutional_flow`'s `rolling_net` already pre-sums exactly those 3 windows server-side
 * (see that field's own docstring in `market_tools.py`) — this just picks which window's number
 * feeds each bar, no client-side re-summing of raw rows. */
/** `_net_over_trailing`'s client-side mirror (`market_tools.py`) for the one category
 * (`GOVERNMENT_BANK_BUYSELL`) that has no server-side pre-summed `rolling_net` — the same
 * "trailing N distinct trading days present in rows, null if fewer exist" rule, applied to
 * `get_government_bank_flows`'s raw per-bank rows (summed across every bank sharing a
 * `trade_date` first, since a date can carry more than one bank's row). */
function governmentBankRollingNet(
  rows: readonly { trade_date: string; buy: number; sell: number }[],
  window: number,
): number | null {
  const byDate = new Map<string, number>();
  for (const row of rows) {
    byDate.set(row.trade_date, (byDate.get(row.trade_date) ?? 0) + row.buy - row.sell);
  }
  const orderedDates = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  if (orderedDates.length < window) return null;
  return orderedDates.slice(-window).reduce((sum, date) => sum + (byDate.get(date) ?? 0), 0);
}

function InstitutionalSummaryBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [window, setWindow] = useState<RollingNetWindow>("5d");
  const query = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const bankQuery = useQuery({
    queryKey: ["stock_government_bank_flows", symbol, market],
    // `get_government_bank_flows`'s own upstream is single-day-per-call, fanned out server-side
    // and capped at 10 trading days (same shape/cap as `get_chip_flows`'s own 分點 fan-out) — 14
    // calendar days back is enough buffer to land on 10 real trading days even across a
    // long weekend, without requesting a range the backend would silently truncate anyway.
    queryFn: () => getGovernmentBankFlows(symbol, market, { startDate: isoDaysAgo(14) }),
  });

  if (query.isLoading || bankQuery.isLoading) return <CardSkeletonBody height={200} />;
  const rollingNet = query.data?.rolling_net ?? [];
  if (rollingNet.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const bankData = bankQuery.data;
  const bankLocked = bankData != null && isMaskedTeaser(bankData);
  const bankRows = bankData != null && !isMaskedTeaser(bankData) ? bankData.rows : [];
  const bankRollingRow =
    bankRows.length > 0
      ? {
          investor: GOVERNMENT_BANK_INVESTOR_KEY,
          net_5d: governmentBankRollingNet(bankRows, 5),
          net_10d: governmentBankRollingNet(bankRows, 10),
          net_20d: governmentBankRollingNet(bankRows, 20),
        }
      : null;
  const allRollingNet = bankRollingRow ? [...rollingNet, bankRollingRow] : rollingNet;

  const netForWindow = (row: (typeof allRollingNet)[number]): number | null | undefined =>
    window === "5d" ? row.net_5d : window === "10d" ? row.net_10d : row.net_20d;

  // A category with no real activity in any of the 3 rolling windows renders as a bare
  // zero-height bar with a label and nothing else. Two distinct "no data" shapes from the
  // backend's own `_net_over_trailing` (market_tools.py, and this file's own
  // `governmentBankRollingNet` mirror for 八大行庫): `null` when fewer than the window's
  // trading days exist yet (八大行庫's own fan-out is capped at 10 days, so its `net_20d` is
  // always `null` — same "not enough history for this window yet" case any category can hit),
  // and a genuine `0` when the days exist but that category's buy-sell summed to exactly zero
  // across every one of them (confirmed live — 外資自行買賣 renders a literal 0 row for
  // symbols with no foreign-dealer-desk activity in this window). Both are dropped the same
  // way, rather than shown as a bar with nothing in it — but only when ALL 3 windows agree
  // there's nothing there; a category real in 5d/10d but not-yet-20d-eligible still shows.
  const isEmptyRow = (row: (typeof allRollingNet)[number]): boolean =>
    (row.net_5d == null || row.net_5d === 0) &&
    (row.net_10d == null || row.net_10d === 0) &&
    (row.net_20d == null || row.net_20d === 0);
  const points = allRollingNet
    .filter((row) => !isEmptyRow(row) && netForWindow(row) != null)
    .map((row) => ({
      investor: row.investor,
      net: netForWindow(row) ?? 0,
      net_5d: row.net_5d,
      net_10d: row.net_10d,
      net_20d: row.net_20d,
    }));
  if (points.length === 0 && !bankLocked) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }
  // Data sheet shows the translated investor name as its own row value (never the raw
  // PascalCase code) — decoupled from `points`, which keeps the raw code for the chart's own
  // `dataKey`/color-map keying (`ChartWithDataSheet`'s `rows` and `children` are independent
  // props; nothing requires them to share one array).
  const sheetRows = points.map((point) => ({
    investor: investorLabel(tFields, point.investor),
    net: point.net,
    net_5d: point.net_5d,
    net_10d: point.net_10d,
    net_20d: point.net_20d,
  }));

  return (
    <div>
      <SubTabRow
        tabs={ROLLING_NET_WINDOWS}
        active={window}
        onChange={setWindow}
        labelFor={(w) => t(`rollingNetWindow.${w}`)}
      />
      <ChartWithDataSheet rows={sheetRows}>
        <div style={{ width: "100%", height: 180 }}>
          <ResponsiveContainer>
            <BarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="investor"
                tickFormatter={(investor: string) => investorLabel(tFields, investor)}
                tick={<InvestorTick tFields={tFields} colors={INVESTOR_CATEGORY_COLORS} />}
                stroke="var(--border)"
              />
              <YAxis
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                tickFormatter={(v: number) => formatCompactNumber(v, locale)}
                width={48}
                stroke="var(--border)"
              />
              <ReferenceLine y={0} stroke="var(--border)" />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(value) => formatNumber(Number(value), locale)}
                labelFormatter={(label) => investorLabel(tFields, String(label))}
              />
              <Bar dataKey="net" radius={3}>
                {points.map((point) => (
                  <Cell
                    key={point.investor}
                    fill={INVESTOR_CATEGORY_COLORS[point.investor] ?? "var(--chart-6)"}
                    aria-label={
                      point.net >= 0
                        ? `${investorLabel(tFields, point.investor)} ▲ ${formatNumber(point.net, locale)}`
                        : `${investorLabel(tFields, point.investor)} ▼ ${formatNumber(point.net, locale)}`
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartWithDataSheet>
      {bankLocked && bankData && isMaskedTeaser(bankData) ? (
        <LockedCategoryChip
          label={investorLabel(tFields, GOVERNMENT_BANK_INVESTOR_KEY)}
          color={INVESTOR_CATEGORY_COLORS[GOVERNMENT_BANK_INVESTOR_KEY] ?? "var(--chart-6)"}
          preview={bankData.preview}
          requiredTier={bankData.required_tier}
        />
      ) : null}
    </div>
  );
}

/** A single Sponsor-locked category alongside 摘要's 法人籌碼 chart (currently: 八大行庫) — a
 * partial-card teaser distinct from `SponsorTeaser`'s whole-card blur, since the rest of this
 * chart's categories are real public data the caller can already see. Shows the category's own
 * label/color (matching what an unlocked bar would look like) with the number itself blurred +
 * a lock chip, never a fabricated value in its place. */
function LockedCategoryChip({
  label,
  color,
  preview,
  requiredTier,
}: {
  readonly label: string;
  readonly color: string;
  readonly preview: string;
  readonly requiredTier: "premium" | "pro";
}): ReactNode {
  const t = useTranslations("market.teaser");
  return (
    <div
      className="mt-3 flex items-center gap-3 rounded-[var(--radius-input)] border p-2.5"
      style={{
        borderColor: `color-mix(in srgb, ${color} 35%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${color} 10%, transparent)`,
      }}
    >
      <span
        aria-hidden
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)]"
      >
        <LockIcon size={14} weight="regular" className="text-[var(--text-muted)]" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-[12px]" style={{ color }}>
          {label}
        </p>
        <p className="truncate text-[11px] text-[var(--text-muted)]">{preview}</p>
      </div>
      <Link
        href="/settings/billing"
        className="shrink-0 whitespace-nowrap text-[12px] text-[var(--accent-text)] hover:underline"
      >
        {t("cta", { tier: t(`tier.${requiredTier}`) })}
      </Link>
    </div>
  );
}

/** 摘要's 融資券 compact stats grid — the reference app's own 摘要表 shows 融資增減/融券增減/
 * 融資餘額/融券餘額/融資使用率/融券使用率/當沖總和/成交量比重/券資比/借券賣出 as a dense number
 * grid beneath the chip-flow chart, sourced from the *latest single row* of each series (a
 * point-in-time snapshot, not a trend) — distinct from `MarginShortColumn`'s own trend charts
 * below, which chart the same tools over time. 成交量比重 (當沖 volume ÷ that day's total traded
 * volume) has no backing field on either side — every other stock-detail card in this codebase
 * already drops fields with no real source rather than fabricate one, so it's omitted here too. */
function MarginShortSnapshot({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const locale = useLocale();
  const marginShortQuery = useQuery({
    queryKey: ["stock_margin_short", symbol, market],
    queryFn: () => getStockMarginShort(symbol, market),
  });
  const lendingQuery = useQuery({
    queryKey: ["securities_lending", symbol, market],
    queryFn: () => getSecuritiesLending(symbol, market),
  });
  const dayTradingQuery = useQuery({
    queryKey: ["stock_day_trading", symbol, market],
    queryFn: () => getStockDayTrading(symbol, market),
  });

  if (marginShortQuery.isLoading || lendingQuery.isLoading || dayTradingQuery.isLoading) {
    return <CardSkeletonBody height={160} />;
  }

  const marginRows = [...(marginShortQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestMargin = marginRows.at(-1);
  if (!latestMargin) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const lendingRows = [...(lendingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestLending = lendingRows.at(-1);
  const dayTradingRows = [...(dayTradingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestDayTrading = dayTradingRows.at(-1);

  const marginChange =
    latestMargin.margin_purchase_today_balance - latestMargin.margin_purchase_yesterday_balance;
  const shortChange =
    latestMargin.short_sale_today_balance - latestMargin.short_sale_yesterday_balance;
  const marginUsage =
    latestMargin.margin_purchase_limit > 0
      ? (latestMargin.margin_purchase_today_balance / latestMargin.margin_purchase_limit) * 100
      : null;
  const shortUsage =
    latestMargin.short_sale_limit > 0
      ? (latestMargin.short_sale_today_balance / latestMargin.short_sale_limit) * 100
      : null;
  const shortToMarginRatio =
    latestMargin.margin_purchase_today_balance > 0
      ? (latestMargin.short_sale_today_balance / latestMargin.margin_purchase_today_balance) * 100
      : null;

  return (
    <div className="grid grid-cols-2 gap-3 font-num text-[13px] tabular-nums">
      <SnapshotStat
        label={t("marginChange")}
        value={formatSignedNumber(marginChange, locale)}
        tone={marginChange >= 0 ? "up" : "down"}
      />
      <SnapshotStat
        label={t("shortChange")}
        value={formatSignedNumber(shortChange, locale)}
        tone={shortChange >= 0 ? "up" : "down"}
      />
      <SnapshotStat
        label={t("marginBalance")}
        value={formatNumber(latestMargin.margin_purchase_today_balance, locale)}
      />
      <SnapshotStat
        label={t("shortBalance")}
        value={formatNumber(latestMargin.short_sale_today_balance, locale)}
      />
      <SnapshotStat
        label={t("marginUsage")}
        value={marginUsage != null ? `${marginUsage.toFixed(2)}%` : "—"}
      />
      <SnapshotStat
        label={t("shortUsage")}
        value={shortUsage != null ? `${shortUsage.toFixed(2)}%` : "—"}
      />
      <SnapshotStat
        label={t("dayTradingVolume")}
        value={latestDayTrading ? formatNumber(latestDayTrading.volume, locale) : "—"}
      />
      <SnapshotStat
        label={t("shortToMarginRatio")}
        value={shortToMarginRatio != null ? `${shortToMarginRatio.toFixed(2)}%` : "—"}
      />
      <SnapshotStat
        label={t("lendingSellVolume")}
        value={latestLending ? formatNumber(latestLending.volume, locale) : "—"}
      />
    </div>
  );
}

function SnapshotStat({
  label,
  value,
  tone,
}: {
  readonly label: string;
  readonly value: string;
  readonly tone?: "up" | "down";
}): ReactNode {
  return (
    <div>
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p
        className="font-num tabular-nums"
        style={{ color: tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : undefined }}
      >
        {value}
      </p>
    </div>
  );
}

function formatSignedNumber(value: number, locale: string): string {
  const sign = value >= 0 ? "▲" : "▼";
  return `${sign} ${formatNumber(Math.abs(value), locale)}`;
}

const HOLDINGS_TABLE_COLLAPSED_ROW_COUNT = 6;

/** 三大法人's 持股 sub-tab — 外資's line is REAL holding shares (`get_foreign_shareholding`'s own
 * `foreign_investment_shares`, the only per-stock holding-BALANCE dataset any configured source
 * exposes — confirmed live: neither FinMind, TWSE OpenAPI, nor TPEx OpenAPI publishes a 投信/
 * 自營商 per-stock holding-balance equivalent; Taiwan's disclosure regime mandates a per-stock
 * foreign-holding-ratio filing with no domestic-institutional counterpart). 投信/自營商 have no
 * such filing, so their lines are an ESTIMATE — cumulative net buy/sell from
 * `get_institutional_flow`, rebased to 0 at the window's first date (a running total of real
 * daily flow, not a fabricated absolute holding count) — explicitly labeled as such via
 * `estimateCaveat` (a permanent, non-removable caveat here, same non-negotiable-caveat rule
 * web/CLAUDE.md §4 applies everywhere else) rather than presented as equally real to 外資's line.
 * Table below mirrors the chart 1:1 — 外資's real 張數+% columns, 投信/自營商's estimated
 * cumulative-net columns, both sharing this component's own merge so chart and table can't
 * diverge. */
function ForeignShareholdingBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  const shareholdingQuery = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market],
    queryFn: () => getForeignShareholding(symbol, market),
  });
  const flowQuery = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const priceQuery = useQuery({
    queryKey: ["price_history", symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS],
    queryFn: () => getPriceHistory([symbol], market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS),
  });

  if (shareholdingQuery.isLoading || flowQuery.isLoading) return <CardSkeletonBody height={280} />;
  const shareholdingRows = [...(shareholdingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const flowRows = flowQuery.data?.rows ?? [];
  if (shareholdingRows.length === 0 && flowRows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const foreignByDate = new Map<string, { shares: number; ratio: number }>();
  for (const row of shareholdingRows) {
    foreignByDate.set(row.trade_date, {
      shares: row.foreign_investment_shares,
      ratio: row.foreign_investment_shares_ratio,
    });
  }
  // `number_of_shares_issued` (total shares outstanding) rides along on every
  // `get_foreign_shareholding` row — reused here as the denominator for 投信/自營商's estimated
  // %-of-shares-outstanding, the same real figure `foreign_investment_shares_ratio` itself is
  // computed from upstream, just applied to the cumulative-flow estimate instead of a real
  // holding count.
  const sharesIssued = shareholdingRows.at(-1)?.number_of_shares_issued;
  const flowByDate = bucketedFlowByDate(flowRows);
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  const dates = [...new Set([...foreignByDate.keys(), ...flowByDate.keys()])].sort((a, b) =>
    a.localeCompare(b),
  );
  let trustCumulative = 0;
  let dealerCumulative = 0;
  const trustEstimateByDate = new Map<string, number>();
  const dealerEstimateByDate = new Map<string, number>();
  for (const date of dates) {
    const bucket = flowByDate.get(date);
    trustCumulative += bucket?.trust ?? 0;
    dealerCumulative += bucket?.dealer ?? 0;
    trustEstimateByDate.set(date, trustCumulative);
    dealerEstimateByDate.set(date, dealerCumulative);
  }

  const points = dates.map((trade_date) => ({
    trade_date,
    foreign_shares: foreignByDate.get(trade_date)?.shares ?? null,
    trust_estimate: trustEstimateByDate.get(trade_date) ?? null,
    dealer_estimate: dealerEstimateByDate.get(trade_date) ?? null,
    price: priceByDate.get(trade_date) ?? null,
  }));
  const tableDatesDesc = [...dates].reverse();

  return (
    <>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="holdings"
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
              formatter={(value) => formatNumber(Number(value), locale)}
            />
            <Legend
              formatter={(value) => {
                if (value === "price") return tFields("fields.close");
                if (value === "foreign_shares") return bucketLabel(t, "foreign");
                if (value === "trust_estimate")
                  return t("estimateLegend", { label: bucketLabel(t, "trust") });
                return t("estimateLegend", { label: bucketLabel(t, "dealer") });
              }}
              wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
            />
            <Line
              yAxisId="holdings"
              type="monotone"
              dataKey="foreign_shares"
              name="foreign_shares"
              stroke={BUCKET_COLORS.foreign}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
            <Line
              yAxisId="holdings"
              type="monotone"
              dataKey="trust_estimate"
              name="trust_estimate"
              stroke={BUCKET_COLORS.trust}
              strokeWidth={2}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
            />
            <Line
              yAxisId="holdings"
              type="monotone"
              dataKey="dealer_estimate"
              name="dealer_estimate"
              stroke={BUCKET_COLORS.dealer}
              strokeWidth={2}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
            />
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name="price"
              stroke="var(--chart-9)"
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4">
        <HoldingsTable
          dates={tableDatesDesc.slice(0, HOLDINGS_TABLE_COLLAPSED_ROW_COUNT)}
          foreignByDate={foreignByDate}
          trustEstimateByDate={trustEstimateByDate}
          dealerEstimateByDate={dealerEstimateByDate}
          sharesIssued={sharesIssued}
          locale={locale}
        />
      </div>

      {tableDatesDesc.length > HOLDINGS_TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: tableDatesDesc.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("institutionalSubTab.holdings")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <HoldingsTable
          dates={tableDatesDesc}
          foreignByDate={foreignByDate}
          trustEstimateByDate={trustEstimateByDate}
          dealerEstimateByDate={dealerEstimateByDate}
          sharesIssued={sharesIssued}
          locale={locale}
        />
      </ExpandModal>
    </>
  );
}

function estimatedSharesCell(
  cumulativeNet: number | undefined,
  sharesIssued: number | undefined,
  locale: string,
): string {
  if (cumulativeNet == null) return "—";
  const sharesText = formatNumber(cumulativeNet, locale);
  if (!sharesIssued || sharesIssued <= 0) return sharesText;
  return `${sharesText} (${((cumulativeNet / sharesIssued) * 100).toFixed(2)}%)`;
}

function HoldingsTable({
  dates,
  foreignByDate,
  trustEstimateByDate,
  dealerEstimateByDate,
  sharesIssued,
  locale,
}: {
  readonly dates: readonly string[];
  readonly foreignByDate: ReadonlyMap<string, { shares: number; ratio: number }>;
  readonly trustEstimateByDate: ReadonlyMap<string, number>;
  readonly dealerEstimateByDate: ReadonlyMap<string, number>;
  readonly sharesIssued: number | undefined;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("flowTable.date")}</th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.foreign }}
            >
              {bucketLabel(t, "foreign")}
            </th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.trust }}
            >
              {t("estimateLegend", { label: bucketLabel(t, "trust") })}
            </th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.dealer }}
            >
              {t("estimateLegend", { label: bucketLabel(t, "dealer") })}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {dates.map((date) => {
            const foreign = foreignByDate.get(date);
            return (
              <tr key={date}>
                <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{date}</td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {foreign
                    ? `${formatNumber(foreign.shares, locale)} (${foreign.ratio.toFixed(2)}%)`
                    : "—"}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text-muted)] tabular-nums">
                  {estimatedSharesCell(trustEstimateByDate.get(date), sharesIssued, locale)}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text-muted)] tabular-nums">
                  {estimatedSharesCell(dealerEstimateByDate.get(date), sharesIssued, locale)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const MARGIN_TABLE_COLLAPSED_ROW_COUNT = 6;

/** One 融資券 sub-tab's shared shell — a day/bar-or-line combo chart (the sub-tab's own metric,
 * colored per `barColor`, plus a 股價 line on a secondary axis) over `priceLookbackDays`, and a
 * detail table below mirroring the reference app's own 融資/融券/券資比/借券/當沖 layout (every
 * sub-tab there pairs its chart with a per-day numeric table, never a chart alone). `renderRow`
 * builds one table row's cells from that day's raw row — kept as a prop so each sub-tab's own
 * (real, already-fetched) column set doesn't have to be hardcoded into this shell. */
function MarginShortChartShell<TRow extends { trade_date: string }>({
  rows,
  barKey,
  barColor,
  barName,
  barTickFormatter,
  priceByDate,
  tableHeaders,
  renderRow,
}: {
  readonly rows: readonly TRow[];
  readonly barKey: keyof TRow & string;
  readonly barColor: string;
  readonly barName: string;
  readonly barTickFormatter: (value: number) => string;
  readonly priceByDate: ReadonlyMap<string, number>;
  readonly tableHeaders: readonly string[];
  readonly renderRow: (row: TRow) => ReactNode;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);

  const points = rows.map((row) => ({
    ...row,
    price: priceByDate.get(row.trade_date) ?? null,
  }));
  const rowsDesc = [...rows].reverse();

  return (
    <>
      {/* 240px — matches 三大法人's 進出 chart exactly (chip-analysis-panel.tsx's other
       * ComposedChart) so the two side-by-side cards' charts (and everything below them) align,
       * not just visually similar heights that drift the table/button rows out of sync. */}
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="metric"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              tickFormatter={barTickFormatter}
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
              formatter={(value, name) => [
                name === "price"
                  ? formatNumber(Number(value), locale)
                  : barTickFormatter(Number(value)),
                name === "price" ? tFields("fields.close") : barName,
              ]}
            />
            <Legend
              formatter={(value) => (value === "price" ? tFields("fields.close") : barName)}
              wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
            />
            <Bar
              yAxisId="metric"
              dataKey={barKey as string}
              name={barKey as string}
              fill={barColor}
              radius={2}
            />
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name="price"
              stroke="var(--chart-9)"
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
              {tableHeaders.map((header) => (
                <th key={header} className="px-2 py-1.5 text-right font-medium first:text-left">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {rowsDesc.slice(0, MARGIN_TABLE_COLLAPSED_ROW_COUNT).map(renderRow)}
          </tbody>
        </table>
      </div>

      {rowsDesc.length > MARGIN_TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: rowsDesc.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={barName}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
              {tableHeaders.map((header) => (
                <th key={header} className="px-2 py-1.5 text-right font-medium first:text-left">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">{rowsDesc.map(renderRow)}</tbody>
        </table>
      </ExpandModal>
    </>
  );
}

function MarginShortBody({
  symbol,
  market,
  view,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly view: MarginSubTab;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const locale = useLocale();
  const marginShortQuery = useQuery({
    queryKey: ["stock_margin_short", symbol, market],
    queryFn: () => getStockMarginShort(symbol, market),
    enabled: view !== "dayTrading" && view !== "lending",
  });
  const dayTradingQuery = useQuery({
    queryKey: ["stock_day_trading", symbol, market],
    queryFn: () => getStockDayTrading(symbol, market),
    enabled: view === "dayTrading",
  });
  const priceQuery = useQuery({
    queryKey: ["price_history", symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS],
    queryFn: () => getPriceHistory([symbol], market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS),
  });
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  if (view === "lending") {
    return <SecuritiesLendingBody symbol={symbol} market={market} priceByDate={priceByDate} />;
  }

  if (view === "dayTrading") {
    if (dayTradingQuery.isLoading) return <CardSkeletonBody height={220} />;
    const dayTradingRows = [...(dayTradingQuery.data?.rows ?? [])].sort((a, b) =>
      a.trade_date.localeCompare(b.trade_date),
    );
    if (dayTradingRows.length === 0) {
      return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
    }
    return <DayTradingBody rows={dayTradingRows} priceByDate={priceByDate} t={t} locale={locale} />;
  }

  if (marginShortQuery.isLoading) return <CardSkeletonBody height={220} />;
  const rows = [...(marginShortQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  if (view === "ratio") {
    return <ShortToMarginRatioBody rows={rows} priceByDate={priceByDate} t={t} locale={locale} />;
  }

  const isShort = view === "short";
  const points = rows.map((row) => {
    const balance = isShort ? row.short_sale_today_balance : row.margin_purchase_today_balance;
    const limit = isShort ? row.short_sale_limit : row.margin_purchase_limit;
    return {
      trade_date: row.trade_date,
      balance,
      change: isShort
        ? row.short_sale_today_balance - row.short_sale_yesterday_balance
        : row.margin_purchase_today_balance - row.margin_purchase_yesterday_balance,
      usage: limit > 0 ? (balance / limit) * 100 : null,
      cashRepayment: isShort ? row.short_sale_cash_repayment : row.margin_purchase_cash_repayment,
    };
  });

  return (
    <MarginShortChartShell
      rows={points}
      barKey="balance"
      barColor={isShort ? "var(--chart-4)" : "var(--chart-1)"}
      barName={isShort ? t("shortBalance") : t("marginBalance")}
      barTickFormatter={(v) => formatCompactNumber(v, locale)}
      priceByDate={priceByDate}
      tableHeaders={[
        t("flowTable.date"),
        isShort ? t("shortBalance") : t("marginBalance"),
        isShort ? t("shortChange") : t("marginChange"),
        isShort ? t("shortUsage") : t("marginUsage"),
        isShort ? t("shortCashRepayment") : t("marginCashRepayment"),
      ]}
      renderRow={(row) => (
        <tr key={row.trade_date}>
          <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{row.trade_date}</td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.balance, locale)}
          </td>
          <NetFlowCell value={row.change} locale={locale} />
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {row.usage != null ? `${row.usage.toFixed(2)}%` : "—"}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.cashRepayment, locale)}
          </td>
        </tr>
      )}
    />
  );
}

/** 融資券's 當沖 sub-tab — pulled out of `MarginShortBody` (same reason `SecuritiesLendingBody`
 * already is its own function) purely to keep that switch-on-`view` function's own cognitive
 * complexity under the linter's threshold; no behavior change from when this was inline. */
function DayTradingBody({
  rows,
  priceByDate,
  t,
  locale,
}: {
  readonly rows: readonly DayTradingRow[];
  readonly priceByDate: ReadonlyMap<string, number>;
  readonly t: ReturnType<typeof useTranslations>;
  readonly locale: string;
}): ReactNode {
  return (
    <MarginShortChartShell
      rows={rows}
      barKey="volume"
      barColor="var(--chart-6)"
      barName={t("dayTradingVolume")}
      barTickFormatter={(v) => formatCompactNumber(v, locale)}
      priceByDate={priceByDate}
      tableHeaders={[
        t("flowTable.date"),
        t("dayTradingVolume"),
        t("dayTradingBuyAmount"),
        t("dayTradingSellAmount"),
      ]}
      renderRow={(row) => (
        <tr key={row.trade_date}>
          <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{row.trade_date}</td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.volume, locale)}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.buy_amount, locale)}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.sell_amount, locale)}
          </td>
        </tr>
      )}
    />
  );
}

/** 融資券's 券資比 sub-tab — pulled out of `MarginShortBody` for the same cognitive-complexity
 * reason `DayTradingBody` above is. No separate 券資比 tool exists — `ratio` is the
 * client-computed ratio of the same `get_margin_short` row's own two balance fields. */
function ShortToMarginRatioBody({
  rows,
  priceByDate,
  t,
  locale,
}: {
  readonly rows: readonly MarginShortRow[];
  readonly priceByDate: ReadonlyMap<string, number>;
  readonly t: ReturnType<typeof useTranslations>;
  readonly locale: string;
}): ReactNode {
  const points = rows.map((row) => ({
    trade_date: row.trade_date,
    ratio:
      row.margin_purchase_today_balance > 0
        ? (row.short_sale_today_balance / row.margin_purchase_today_balance) * 100
        : 0,
    margin_balance: row.margin_purchase_today_balance,
    short_balance: row.short_sale_today_balance,
  }));
  return (
    <MarginShortChartShell
      rows={points}
      barKey="ratio"
      barColor="var(--chart-5)"
      barName={t("shortToMarginRatio")}
      barTickFormatter={(v) => `${v.toFixed(1)}%`}
      priceByDate={priceByDate}
      tableHeaders={[
        t("flowTable.date"),
        t("shortToMarginRatio"),
        t("marginBalance"),
        t("shortBalance"),
      ]}
      renderRow={(row) => (
        <tr key={row.trade_date}>
          <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{row.trade_date}</td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {row.ratio.toFixed(2)}%
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.margin_balance, locale)}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.short_balance, locale)}
          </td>
        </tr>
      )}
    />
  );
}

function SecuritiesLendingBody({
  symbol,
  market,
  priceByDate,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly priceByDate: ReadonlyMap<string, number>;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["securities_lending", symbol, market],
    queryFn: () => getSecuritiesLending(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={220} />;
  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }
  // `securities_lending` is one row per (trade_date, transaction_type) — e.g. 競價/定價 can both
  // land on the same day — so a plain 1:1 row-to-point map produces duplicate `trade_date` keys
  // (a live React "two children with the same key" warning caught this). Sum same-day rows
  // first, same grouping pass `bucketedFlowByDate` already does for 三大法人.
  const byDate = new Map<string, { volume: number; feeRateSum: number; feeRateCount: number }>();
  for (const row of rows) {
    const entry = byDate.get(row.trade_date) ?? { volume: 0, feeRateSum: 0, feeRateCount: 0 };
    entry.volume += row.volume;
    entry.feeRateSum += row.fee_rate;
    entry.feeRateCount += 1;
    byDate.set(row.trade_date, entry);
  }
  const datesAsc = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  // 借券成交 has no running-balance field on this side (每筆為單日成交量, not a balance) — the
  // reference app's own 借券 view plots a cumulative sell-balance line; this mirrors that via a
  // running sum of daily `volume`, the same real-flow-summed-into-a-balance pattern
  // `ForeignShareholdingBody`'s 投信/自營商 estimate lines already use elsewhere in this panel.
  let cumulative = 0;
  const points = datesAsc.map((trade_date) => {
    const entry = byDate.get(trade_date);
    const volume = entry?.volume ?? 0;
    cumulative += volume;
    return {
      trade_date,
      volume,
      balance: cumulative,
      fee_rate: entry && entry.feeRateCount > 0 ? entry.feeRateSum / entry.feeRateCount : 0,
    };
  });

  return (
    <MarginShortChartShell
      rows={points}
      barKey="balance"
      barColor="var(--chart-5)"
      barName={t("lendingSellVolume")}
      barTickFormatter={(v) => formatCompactNumber(v, locale)}
      priceByDate={priceByDate}
      tableHeaders={[
        t("flowTable.date"),
        t("lendingSellVolume"),
        t("lendingDailyVolume"),
        t("lendingFeeRate"),
      ]}
      renderRow={(row) => (
        <tr key={row.trade_date}>
          <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{row.trade_date}</td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.balance, locale)}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {formatNumber(row.volume, locale)}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {row.fee_rate.toFixed(2)}%
          </td>
        </tr>
      )}
    />
  );
}

/** Per-broker aggregate over the fetched window — `net`/`buy`/`sell` summed across every day
 * that broker appears in `rows`, plus its share of the window's total reported 分點 volume
 * (`成交比重`). Computed client-side from `ChipBrokerRow`'s real per-day fields — never a
 * separate fetch, since `get_chip_flows` already returns every row for the whole range. */
function aggregateBrokers(
  rows: readonly { broker_id: string; broker_name: string; buy: number; sell: number }[],
): { broker_id: string; broker_name: string; net: number; buy: number; sell: number }[] {
  const byBroker = new Map<
    string,
    { broker_name: string; net: number; buy: number; sell: number }
  >();
  for (const row of rows) {
    const entry = byBroker.get(row.broker_id) ?? {
      broker_name: row.broker_name,
      net: 0,
      buy: 0,
      sell: 0,
    };
    entry.net += row.buy - row.sell;
    entry.buy += row.buy;
    entry.sell += row.sell;
    byBroker.set(row.broker_id, entry);
  }
  return [...byBroker.entries()].map(([broker_id, v]) => ({ broker_id, ...v }));
}

/** `ChipFlowsBody`'s own derived-stat block, pulled into a pure function purely to keep that
 * component's cognitive complexity under the linter's threshold — same inputs/outputs, no
 * behavior change from when this was inline. See `ChipFlowsBody`'s own docstring for what each
 * of these figures means and why. */
function computeChipFlowStats(
  rows: readonly {
    trade_date: string;
    broker_id: string;
    broker_name: string;
    buy: number;
    sell: number;
  }[],
  sharesIssued: number | undefined,
): {
  brokers: ReturnType<typeof aggregateBrokers>;
  totalVolume: number;
  mainForceNet: number;
  sharesOfCapital: number | null;
  turnoverRatio: number | null;
  latestTradeDate: string | undefined;
  concentrationDaysPct: number | null;
  buyTop: ReturnType<typeof aggregateBrokers>;
  sellTop: ReturnType<typeof aggregateBrokers>;
} {
  const brokers = aggregateBrokers(rows);
  const totalVolume = brokers.reduce((sum, b) => sum + b.buy + b.sell, 0);
  const topByMagnitude = [...brokers]
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
    .slice(0, TOP_BROKER_ROW_COUNT);
  const mainForceNet = topByMagnitude.reduce((sum, b) => sum + b.net, 0);
  const overallNet = brokers.reduce((sum, b) => sum + b.net, 0);

  const sharesOfCapital =
    sharesIssued && sharesIssued > 0 ? (mainForceNet / sharesIssued) * 100 : null;
  const turnoverRatio =
    sharesIssued && sharesIssued > 0 ? (totalVolume / sharesIssued) * 100 : null;
  const latestTradeDate = [...new Set(rows.map((row) => row.trade_date))]
    .sort((a, b) => a.localeCompare(b))
    .at(-1);

  const byDateNet = new Map<string, number>();
  for (const row of rows) {
    byDateNet.set(row.trade_date, (byDateNet.get(row.trade_date) ?? 0) + row.buy - row.sell);
  }
  const dailyNets = [...byDateNet.values()];
  const sameSignedDays = dailyNets.filter(
    (net) => (net >= 0 && overallNet >= 0) || (net < 0 && overallNet < 0),
  ).length;
  const concentrationDaysPct =
    dailyNets.length > 0 ? (sameSignedDays / dailyNets.length) * 100 : null;

  const buyTop = [...brokers]
    .filter((b) => b.net > 0)
    .sort((a, b) => b.net - a.net)
    .slice(0, TOP_BROKER_ROW_COUNT);
  const sellTop = [...brokers]
    .filter((b) => b.net < 0)
    .sort((a, b) => a.net - b.net)
    .slice(0, TOP_BROKER_ROW_COUNT);

  return {
    brokers,
    totalVolume,
    mainForceNet,
    sharesOfCapital,
    turnoverRatio,
    latestTradeDate,
    concentrationDaysPct,
    buyTop,
    sellTop,
  };
}

/** 分點's 買賣超 tab — period selector (近日/5日/10日, bounded to the backend's per-trading-day
 * fan-out cap — see `BROKER_LOOKBACK_WINDOWS`'s own docstring), a 主力籌碼 stat block, and a
 * 買超Top15/賣超Top15 toggle (one table visible at a time, matching the reference app's own
 * red-highlighted-pill pattern — not both stacked) with 買賣超/買張/賣張/成交比重 columns. 成交
 * 比重 is each broker's share of the window's TOTAL REPORTED 分點 volume (sum of every broker's
 * buy+sell in `rows`), not exchange-wide turnover — `get_chip_flows` only carries branch-level
 * rows, no whole-market volume figure, so this is the honest denominator available. 主力集中% is
 * the top-`TOP_BROKER_ROW_COUNT`-by-magnitude brokers' combined net ÷ that same reported-volume
 * total; 佔股本比重 divides by `get_foreign_shareholding`'s `number_of_shares_issued` (already
 * fetched elsewhere in this panel, real issued-share count); 集中天數 is the % of days within
 * the window where the top-magnitude broker's net stayed same-signed as the window's overall
 * net — a defensible "how consistent was the dominant flow" figure, not a claim of replicating
 * any vendor's proprietary formula. */
function ChipFlowsBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const locale = useLocale();
  const [window, setWindow] = useState<BrokerLookbackWindow>("recent");
  const [direction, setDirection] = useState<BrokerTopDirection>("buy");
  const query = useQuery({
    queryKey: ["stock_chip_flows", symbol, market, window],
    queryFn: () =>
      getStockChipFlows(symbol, market, {
        startDate: isoDaysAgo(BROKER_LOOKBACK_DAYS[window]),
      }),
  });
  const shareholdingQuery = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market],
    queryFn: () => getForeignShareholding(symbol, market),
  });

  // 摘要's sibling column (`SummaryColumn`) stacks two skeletons while loading (`Institutional
  // SummaryBody` 200px + `MarginShortSnapshot` 160px, plus their own subheaders) — this column
  // has only one body under its header, so its skeleton must be taller than its own eventual
  // content height alone or the two cards visibly mismatch height during load (flagged live via
  // screenshot: 分點's card fell short of 摘要's bottom edge).
  if (query.isLoading) return <CardSkeletonBody height={440} />;
  if (!query.data) return null;
  if (isMaskedTeaser(query.data)) {
    return (
      <SponsorTeaser
        headline={t("column.brokers")}
        preview={query.data.preview}
        requiredTier={query.data.required_tier}
      >
        <div className="h-48 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }
  const rows = query.data.rows;
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const sharesIssued = [...(shareholdingQuery.data?.rows ?? [])]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .at(-1)?.number_of_shares_issued;
  const {
    totalVolume,
    mainForceNet,
    sharesOfCapital,
    turnoverRatio,
    latestTradeDate,
    concentrationDaysPct,
    buyTop,
    sellTop,
  } = computeChipFlowStats(rows, sharesIssued);

  return (
    <div>
      <SubTabRow
        tabs={BROKER_LOOKBACK_WINDOWS}
        active={window}
        onChange={setWindow}
        labelFor={(w) => t(`brokerLookbackWindow.${w}`)}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 font-num text-[13px] tabular-nums">
        <SnapshotStat
          label={t("mainForceNet")}
          value={formatSignedNumber(mainForceNet, locale)}
          tone={mainForceNet >= 0 ? "up" : "down"}
        />
        <SnapshotStat
          label={t("mainForceConcentration")}
          value={totalVolume > 0 ? `${((mainForceNet / totalVolume) * 100).toFixed(2)}%` : "—"}
        />
        <SnapshotStat
          label={t("sharesOfCapital")}
          value={sharesOfCapital != null ? `${sharesOfCapital.toFixed(2)}%` : "—"}
        />
        <SnapshotStat
          label={t("concentrationDays")}
          value={concentrationDaysPct != null ? `${concentrationDaysPct.toFixed(0)}%` : "—"}
        />
        <SnapshotStat label={t("brokerTotalVolume")} value={formatNumber(totalVolume, locale)} />
        <SnapshotStat
          label={t("turnoverRatio")}
          value={turnoverRatio != null ? `${turnoverRatio.toFixed(2)}%` : "—"}
        />
      </div>
      <div className="mb-2 flex items-center justify-between">
        <SubTabRow
          tabs={BROKER_TOP_DIRECTIONS}
          active={direction}
          onChange={setDirection}
          labelFor={(d) => t(d === "buy" ? "buyTop15" : "sellTop15")}
        />
        {latestTradeDate ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("asOfDate", { date: latestTradeDate })}
          </span>
        ) : null}
      </div>
      <BrokerTopTable
        title={t(direction === "buy" ? "buyTop15" : "sellTop15")}
        rows={direction === "buy" ? buyTop : sellTop}
        totalVolume={totalVolume}
        locale={locale}
      />
    </div>
  );
}

type BrokerTopRow = {
  broker_id: string;
  broker_name: string;
  net: number;
  buy: number;
  sell: number;
};

const BROKER_TOP_COLLAPSED_ROW_COUNT = 8;

/** 買超Top15/賣超Top15 table — collapsed to `BROKER_TOP_COLLAPSED_ROW_COUNT` rows with an
 * `ExpandModal` for the rest, mirroring `basic-info-panel.tsx`'s own 董監事持股/公告 collapse
 * pattern (same `expandTo`/`collapse` i18n keys, same shared `ExpandModal`) — this card sits
 * beside 摘要 in a 2-column row, and rendering all 15 rows inline made it far taller than its
 * neighbor (a live QA finding: the card bottom overflowed well past 摘要's own height). */
function BrokerTopTable({
  title,
  rows,
  totalVolume,
  locale,
}: {
  readonly title: string;
  readonly rows: readonly BrokerTopRow[];
  readonly totalVolume: number;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const [expanded, setExpanded] = useState(false);

  if (rows.length === 0) {
    return (
      <div>
        <h4 className="mb-2 font-medium text-[13px] text-[var(--text)]">{title}</h4>
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      </div>
    );
  }

  const collapsedRows = rows.slice(0, BROKER_TOP_COLLAPSED_ROW_COUNT);

  return (
    <div>
      <h4 className="mb-2 font-medium text-[13px] text-[var(--text)]">{title}</h4>
      <BrokerTopTableBody rows={collapsedRows} totalVolume={totalVolume} locale={locale} />
      {rows.length > BROKER_TOP_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: rows.length })}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={title}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <BrokerTopTableBody rows={rows} totalVolume={totalVolume} locale={locale} />
      </ExpandModal>
    </div>
  );
}

/** Renders the Top15 rows as one plain table — no `ChartWithDataSheet` wrapper here, unlike
 * every other body in this panel: that component pairs a CHART with its own underlying-rows
 * table as a second, collapsible view of the same data. This body already IS the full data
 * table (every column, every row), so wrapping it would render the identical rows twice — a
 * live QA finding after adding `defaultOpen` elsewhere in this panel made the duplication
 * visible (an expanded "Top15" modal showed the same 15 broker rows back to back). */
function BrokerTopTableBody({
  rows,
  totalVolume,
  locale,
}: {
  readonly rows: readonly BrokerTopRow[];
  readonly totalVolume: number;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
            <th className="py-1.5 text-left font-medium">{t("broker")}</th>
            <th className="py-1.5 text-right font-medium">{t("netFlow")}</th>
            <th className="py-1.5 text-right font-medium">{t("buyLots")}</th>
            <th className="py-1.5 text-right font-medium">{t("sellLots")}</th>
            <th className="py-1.5 text-right font-medium">{t("turnoverShare")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const share = totalVolume > 0 ? ((row.buy + row.sell) / totalVolume) * 100 : 0;
            return (
              <tr key={row.broker_id} className="border-[var(--border)] border-b">
                <td className="py-1.5 text-[var(--text)]">{row.broker_name}</td>
                <td
                  className="py-1.5 text-right font-num tabular-nums"
                  style={{ color: row.net >= 0 ? "var(--up)" : "var(--down)" }}
                >
                  {row.net >= 0 ? "▲ " : "▼ "}
                  {formatNumber(Math.abs(row.net), locale)}
                </td>
                <td className="py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.buy, locale)}
                </td>
                <td className="py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.sell, locale)}
                </td>
                <td className="py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {share.toFixed(2)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
