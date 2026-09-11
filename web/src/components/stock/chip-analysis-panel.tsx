"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
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
import { getPriceHistory } from "@/lib/api/market";
import {
  getForeignShareholding,
  getSecuritiesLending,
  getStockChipFlows,
  getStockDayTrading,
  getStockInstitutionalFlow,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

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
 * per category (the reference app groups into 4 buckets incl. a Sponsor-tier 八大行庫 series we
 * don't have wired to this card; `get_institutional_flow` gives 5 real categories, so 5 distinct
 * colors), never `--up`/`--down`: those are reserved for signed price/%/score deltas, not a
 * category axis (web/CLAUDE.md §4). */
const INVESTOR_CATEGORY_COLORS: Record<string, string> = {
  Foreign_Investor: "var(--chart-1)",
  Investment_Trust: "var(--chart-2)",
  Dealer_self: "var(--chart-3)",
  Dealer_Hedging: "var(--chart-4)",
  Foreign_Dealer_Self: "var(--chart-5)",
};

/** `fieldLabel` resolves through the shared `chat.cards.fields` namespace (not this panel's own
 * `stock.chipAnalysis` one), and its `LABELED_FIELDS` map is keyed lowercase (`foreign_investor`)
 * while the raw `investor` row value arrives PascalCase (`Foreign_Investor`) — lowercase before
 * lookup, same pattern `institutional-flow-timeseries.tsx`'s own `categoryLabel` helper uses. */
function investorLabel(tFields: ReturnType<typeof useTranslations>, investor: string): string {
  return fieldLabel(tFields, investor.toLowerCase());
}

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

/** One `<Line>` series over `trade_date`, wrapped in `ChartWithDataSheet` — the shared shape
 * every single-line 籌碼分析 chart (外資持股比, 融資券 3 of its 4 views) reduces to, differing
 * only in the plotted field, its color/name, and the Y-axis tick/tooltip formatter. Pulled out
 * once so these views can't drift on the chart shell as more get added. */
function SimpleLineChartBody<TPoint extends { trade_date: string }>({
  points,
  dataKey,
  name,
  stroke,
  yAxisTickFormatter,
  tooltipFormatter,
}: {
  readonly points: readonly TPoint[];
  readonly dataKey: keyof TPoint & string;
  readonly name: string;
  readonly stroke: string;
  readonly yAxisTickFormatter: (value: number) => string;
  readonly tooltipFormatter: (value: unknown) => string;
}): ReactNode {
  return (
    <ChartWithDataSheet rows={points as Record<string, unknown>[]}>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <LineChart
            data={points as Record<string, unknown>[]}
            margin={{ left: 8, right: 8, bottom: 8 }}
          >
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
            />
            <YAxis
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              tickFormatter={yAxisTickFormatter}
              width={48}
              stroke="var(--border)"
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} formatter={tooltipFormatter} />
            <Line
              type="monotone"
              dataKey={dataKey}
              name={name}
              stroke={stroke}
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

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
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <SummaryColumn symbol={symbol} market={market} />
        <BrokersColumn symbol={symbol} market={market} />
      </div>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <InstitutionalColumn symbol={symbol} market={market} />
        <MarginShortColumn symbol={symbol} market={market} />
      </div>
    </div>
  );
}

function SummaryColumn({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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

/** 三大法人's 進出 chart — 3 separately-colored bars per day (外資/投信/自營商 net, real
 * per-category rows from `get_institutional_flow`) with a price-line overlay on a secondary
 * axis, matching the reference app's own 進出 chart exactly. Previously summed all 3 categories
 * into one flat net bar per day — a live QA finding pointed out the reference always shows the
 * 3 categories as distinct bars. Price comes from `getPriceHistory` (already used in
 * `AssetHeader`), merged by `trade_date` — a day with a flow row but no matching price bar (a
 * gap either series might have) simply renders `price: null`, which recharts skips as a broken
 * line segment rather than a fabricated zero. */
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

  const categories = [...new Set(rows.map((row) => row.investor))];
  const byDate = new Map<string, Record<string, number>>();
  for (const row of rows) {
    const perDate = byDate.get(row.trade_date) ?? {};
    perDate[row.investor] = (perDate[row.investor] ?? 0) + (row.buy - row.sell);
    byDate.set(row.trade_date, perDate);
  }
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  const points = [...byDate.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([trade_date, perDate]) => ({
      trade_date,
      price: priceByDate.get(trade_date) ?? null,
      ...perDate,
    }));

  return (
    <ChartWithDataSheet rows={points}>
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
              formatter={(value) => formatNumber(Number(value), locale)}
              labelFormatter={(label) => String(label)}
            />
            {categories.map((category, i) => (
              <Bar
                key={category}
                yAxisId="flow"
                dataKey={category}
                name={investorLabel(tFields, category)}
                stackId="flow"
                fill={INVESTOR_CATEGORY_COLORS[category] ?? `var(--chart-${(i % 6) + 1})`}
                radius={2}
              />
            ))}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name={tFields("fields.close")}
              stroke="var(--chart-6)"
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

/** 三大法人's 摘要 sub-tab — stat cards for 三大法人 (combined) + 外資/投信/自營商, showing each
 * category's real NET FLOW total over the fetched window. The reference app's own 摘要 tab shows
 * HOLDING totals here (matching its 持股 tab's numbers) — not built that way because only 外資
 * has a real holding-ratio dataset (`get_foreign_shareholding`); 投信/自營商 have no equivalent,
 * and approximating them via cumulative net-flow summation would not be real ownership data.
 * Net-flow totals are the honest substitute: 100% real, and already the same underlying numbers
 * `InstitutionalFlowBody` charts, just re-presented as a stat-card summary. */
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
  const query = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={160} />;
  const rows = query.data?.rows ?? [];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const totalsByCategory = new Map<string, number>();
  for (const row of rows) {
    totalsByCategory.set(
      row.investor,
      (totalsByCategory.get(row.investor) ?? 0) + row.buy - row.sell,
    );
  }
  const combinedNet = [...totalsByCategory.values()].reduce((sum, v) => sum + v, 0);

  const cards = [
    { key: "combined", label: t("institutionalCombined"), net: combinedNet },
    ...[...totalsByCategory.entries()].map(([investor, net]) => ({
      key: investor,
      label: investorLabel(tFields, investor),
      net,
    })),
  ];

  return (
    <div className="grid grid-cols-2 gap-3 font-num text-[13px] tabular-nums sm:grid-cols-4">
      {cards.map((card) => (
        <SnapshotStat
          key={card.key}
          label={card.label}
          value={formatSignedNumber(card.net, locale)}
          tone={card.net >= 0 ? "up" : "down"}
        />
      ))}
    </div>
  );
}

/** 摘要's 法人籌碼 chart — reference app's own 摘要表 shows one bar chart of net buy/sell per
 * investor category, with a lookback-window selector (5日/10日/20日), not a flat number list.
 * `get_institutional_flow`'s `rolling_net` already pre-sums exactly those 3 windows server-side
 * (see that field's own docstring in `market_tools.py`) — this just picks which window's number
 * feeds each bar, no client-side re-summing of raw rows. */
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

  if (query.isLoading) return <CardSkeletonBody height={200} />;
  const rollingNet = query.data?.rolling_net ?? [];
  if (rollingNet.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const netForWindow = (row: (typeof rollingNet)[number]): number | null | undefined =>
    window === "5d" ? row.net_5d : window === "10d" ? row.net_10d : row.net_20d;

  const points = rollingNet.map((row) => ({
    investor: row.investor,
    net: netForWindow(row) ?? 0,
    net_5d: row.net_5d,
    net_10d: row.net_10d,
    net_20d: row.net_20d,
  }));
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
                tick={{ fill: "var(--text-muted)", fontSize: 10 }}
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

function ForeignShareholdingBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const query = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market],
    queryFn: () => getForeignShareholding(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={220} />;
  const rows = [...(query.data?.rows ?? [])];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }
  const points = rows.map((row) => ({
    trade_date: row.trade_date,
    foreign_ratio: row.foreign_investment_shares_ratio,
  }));

  return (
    <SimpleLineChartBody
      points={points}
      dataKey="foreign_ratio"
      name={t("foreignRatio")}
      stroke="var(--chart-2)"
      yAxisTickFormatter={(v) => `${v.toFixed(1)}%`}
      tooltipFormatter={(value) => `${Number(value).toFixed(2)}%`}
    />
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

  if (view === "lending") {
    return <SecuritiesLendingBody symbol={symbol} market={market} />;
  }

  if (view === "dayTrading") {
    if (dayTradingQuery.isLoading) return <CardSkeletonBody height={220} />;
    const rows = [...(dayTradingQuery.data?.rows ?? [])];
    if (rows.length === 0) {
      return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
    }
    const points = rows.map((row) => ({ trade_date: row.trade_date, volume: row.volume }));
    return (
      <ChartWithDataSheet rows={points}>
        <div style={{ width: "100%", height: 220 }}>
          <ResponsiveContainer>
            <BarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="trade_date"
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
                formatter={(value) => formatNumber(Number(value), locale)}
              />
              <Bar dataKey="volume" fill="var(--chart-6)" radius={3} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartWithDataSheet>
    );
  }

  if (marginShortQuery.isLoading) return <CardSkeletonBody height={220} />;
  const rows = [...(marginShortQuery.data?.rows ?? [])];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  if (view === "ratio") {
    const points = rows.map((row) => ({
      trade_date: row.trade_date,
      // No separate 券資比 tool exists — this is the client-computed ratio of the same
      // `get_margin_short` row's own two balance fields, per this component's own docstring.
      ratio:
        row.margin_purchase_today_balance > 0
          ? (row.short_sale_today_balance / row.margin_purchase_today_balance) * 100
          : 0,
    }));
    return (
      <SimpleLineChartBody
        points={points}
        dataKey="ratio"
        name={t("shortToMarginRatio")}
        stroke="var(--chart-5)"
        yAxisTickFormatter={(v) => `${v.toFixed(1)}%`}
        tooltipFormatter={(value) => `${Number(value).toFixed(2)}%`}
      />
    );
  }

  const dataKey = view === "short" ? "short_balance" : "margin_balance";
  const points = rows.map((row) => ({
    trade_date: row.trade_date,
    margin_balance: row.margin_purchase_today_balance,
    short_balance: row.short_sale_today_balance,
  }));

  return (
    <SimpleLineChartBody
      points={points}
      dataKey={dataKey}
      name={view === "short" ? t("shortBalance") : t("marginBalance")}
      stroke={view === "short" ? "var(--chart-4)" : "var(--chart-1)"}
      yAxisTickFormatter={(v) => formatCompactNumber(v, locale)}
      tooltipFormatter={(value) => formatNumber(Number(value), locale)}
    />
  );
}

function SecuritiesLendingBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["securities_lending", symbol, market],
    queryFn: () => getSecuritiesLending(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={220} />;
  const rows = [...(query.data?.rows ?? [])];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }
  const points = rows.map((row) => ({ trade_date: row.trade_date, volume: row.volume }));

  return (
    <ChartWithDataSheet rows={points}>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <BarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
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
              formatter={(value) => formatNumber(Number(value), locale)}
            />
            <Bar dataKey="volume" fill="var(--chart-5)" radius={4} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
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

  if (query.isLoading) return <CardSkeletonBody height={280} />;
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

  const brokers = aggregateBrokers(rows);
  const totalVolume = brokers.reduce((sum, b) => sum + b.buy + b.sell, 0);
  const rankedByMagnitude = [...brokers].sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  const topByMagnitude = rankedByMagnitude.slice(0, TOP_BROKER_ROW_COUNT);
  const mainForceNet = topByMagnitude.reduce((sum, b) => sum + b.net, 0);
  const overallNet = brokers.reduce((sum, b) => sum + b.net, 0);

  const sharesIssued = [...(shareholdingQuery.data?.rows ?? [])]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .at(-1)?.number_of_shares_issued;
  const sharesOfCapital =
    sharesIssued && sharesIssued > 0 ? (mainForceNet / sharesIssued) * 100 : null;

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
      </div>
      <SubTabRow
        tabs={BROKER_TOP_DIRECTIONS}
        active={direction}
        onChange={setDirection}
        labelFor={(d) => t(d === "buy" ? "buyTop15" : "sellTop15")}
      />
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

const BROKER_TOP_COLLAPSED_ROW_COUNT = 5;

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
