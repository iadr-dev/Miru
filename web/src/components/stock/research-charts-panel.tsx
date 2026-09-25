"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { RangeSelect } from "@/components/stock/range-select";
import { ExpandModal } from "@/components/ui/expand-modal";
import { MonthGridCalendar } from "@/components/ui/month-grid-calendar";
import {
  getAnalystConsensus,
  getStockCalendarEvents,
  getStockValuationRatios,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";
import type { AnalystConsensus, CalendarEventOut, ValuationRatiosRow } from "@/types/stock-detail";

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

function yearsAgoIso(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

function daysFromTodayIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * 研究圖表 tab — 估值河流圖 (`get_valuation_ratios`, PER/PBR band ribbons + current mark),
 * 分析師深度 (`get_analyst_consensus`, coverage-gated: hidden entirely when yfinance tracks no
 * analyst coverage for this symbol, never an empty scaffold — the tool itself returns
 * `consensus: null` rather than fabricating one), and 個股事件 (`get_calendar_events`,
 * Sponsor-tier, same feed-sourced 除權息/現金增資/股東會 types `basic-info-panel.tsx`'s own
 * announcements card already filters to since this tool has no 法說會/財報deadline/結算 data).
 *
 * Deliberately 3 cards, not the design doc's nominal 4: `AnalystConsensus` has no earnings-
 * surprise-history array server-side (only the single latest upgrade/downgrade row) — this tab
 * never fabricates a 財報意外史 timeline the backend doesn't have (per this app's governance:
 * coverage-gated → show nothing, not invented content).
 *
 * `AnalystConsensusCard` is exported and also rendered from `us-stock/overview-panel.tsx` — the
 * US 總覽 tab folds in this one card (real for every market) since 估值河流圖/個股事件 are
 * TW-only and the US 研究 tab was otherwise nearly empty; this panel itself stays unchanged for
 * TW/ETF.
 */
export function ResearchChartsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  // `get_valuation_ratios`/`get_calendar_events` are FinMind/TWSE-sourced with no non-TW
  // equivalent — dropped rather than rendered against the wrong market's data.
  // `AnalystConsensusCard` (`get_analyst_consensus`, yfinance) is real for every market.
  const isTw = market === "TW";
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {isTw ? <ValuationRiverCard symbol={symbol} market={market} /> : null}
      <AnalystConsensusCard symbol={symbol} market={market} />
      {isTw ? <StockEventsCard symbol={symbol} market={market} /> : null}
    </div>
  );
}

const VALUATION_RANGE_YEARS = { "1y": 1, "3y": 3, "5y": 5 } as const;
type ValuationRange = keyof typeof VALUATION_RANGE_YEARS;
const VALUATION_RANGE_OPTIONS = Object.keys(VALUATION_RANGE_YEARS) as ValuationRange[];

/** 估值河流圖 — PER/PBR each as its own band ribbon (min-max `Area` at low alpha) with the
 * current reading as a bold line over it, `--chart-1`/`--chart-3` respectively (categorical
 * multiples, never `--up`/`--down` — a PER isn't a signed price move). Two independently-scaled
 * y-axes since PER (~10-30) and PBR (~1-5) share no sensible common scale. */
function ValuationRiverCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.research");
  const locale = useLocale();
  const [range, setRange] = useState<ValuationRange>("3y");
  const [expanded, setExpanded] = useState(false);
  const startDate = yearsAgoIso(VALUATION_RANGE_YEARS[range]);
  const query = useQuery({
    queryKey: ["valuation_ratios", symbol, market, range],
    queryFn: () => getStockValuationRatios(symbol, market, { startDate }),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <CardSkeletonBody height={340} />
      </section>
    );
  }

  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const band = query.data?.band;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("valuationRiverTitle")}</h2>
        <RangeSelect
          value={range}
          onChange={setRange}
          options={VALUATION_RANGE_OPTIONS}
          labelFor={(key) => t(`valuationRange.${key}`)}
          ariaLabel={t("valuationRiverTitle")}
        />
      </div>

      {rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <>
          {band ? <ValuationBandStrip band={band} locale={locale} t={t} /> : null}

          <ValuationChart rows={rows} height={280} locale={locale} />

          <div className="mt-4">
            <ValuationTable
              rows={[...rows].reverse().slice(0, VALUATION_TABLE_COLLAPSED_ROW_COUNT)}
              locale={locale}
              t={t}
            />
          </div>
          {rows.length > VALUATION_TABLE_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className={cn(
                "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              )}
            >
              <span>{t("expandTo", { count: rows.length })}</span>
            </button>
          ) : null}
        </>
      )}

      <ExpandModal
        open={expanded}
        title={t("valuationRiverTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <ValuationChart rows={rows} height={360} locale={locale} />
        <div className="mt-4">
          <ValuationTable rows={[...rows].reverse()} locale={locale} t={t} />
        </div>
      </ExpandModal>
    </section>
  );
}

const VALUATION_TABLE_COLLAPSED_ROW_COUNT = 8;

function ValuationBandStrip({
  band,
  locale,
  t,
}: {
  readonly band: NonNullable<
    NonNullable<Awaited<ReturnType<typeof getStockValuationRatios>>["band"]>
  >;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const items: { label: string; current: string; range: string; color: string }[] = [
    {
      label: t("perStat"),
      current: formatNumber(band.per_current, locale, { decimals: 2 }),
      range: `${formatNumber(band.per_min, locale, { decimals: 1 })} - ${formatNumber(band.per_max, locale, { decimals: 1 })}`,
      color: "var(--chart-1)",
    },
    {
      label: t("pbrStat"),
      current: formatNumber(band.pbr_current, locale, { decimals: 2 }),
      range: `${formatNumber(band.pbr_min, locale, { decimals: 1 })} - ${formatNumber(band.pbr_max, locale, { decimals: 1 })}`,
      color: "var(--chart-3)",
    },
    {
      label: t("dividendYieldStat"),
      current: `${formatNumber(band.dividend_yield_current, locale, { decimals: 2 })}%`,
      range: `${formatNumber(band.dividend_yield_min, locale, { decimals: 1 })}% - ${formatNumber(band.dividend_yield_max, locale, { decimals: 1 })}%`,
      color: "var(--chart-5)",
    },
  ];
  return (
    <div className="mb-4 grid grid-cols-1 gap-3 rounded-[var(--radius-input)] bg-[var(--surface-2)] p-3 sm:grid-cols-3">
      {items.map((item) => (
        <div key={item.label} className="flex flex-col gap-0.5">
          <span className="font-num text-[17px] tabular-nums" style={{ color: item.color }}>
            {item.current}
          </span>
          <span className="text-[11px] text-[var(--text-muted)]">
            {item.label} · {t("bandRangeLabel", { range: item.range })}
          </span>
        </div>
      ))}
    </div>
  );
}

function ValuationChart({
  rows,
  height,
  locale,
}: {
  readonly rows: readonly ValuationRatiosRow[];
  readonly height: number;
  readonly locale: string;
}): ReactNode {
  const points = rows.map((row) => ({
    date: row.trade_date,
    per: row.per,
    pbr: row.pbr,
  }));
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--text-muted)", fontSize: 10 }}
            stroke="var(--border)"
            minTickGap={40}
          />
          <YAxis
            yAxisId="per"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            width={40}
            stroke="var(--border)"
          />
          <YAxis
            yAxisId="pbr"
            orientation="right"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            width={40}
            stroke="var(--border)"
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => formatNumber(Number(value), locale, { decimals: 2 })}
          />
          <Area
            yAxisId="per"
            type="monotone"
            dataKey="per"
            name="PER"
            stroke="var(--chart-1)"
            fill="var(--chart-1)"
            fillOpacity={0.14}
            strokeWidth={2}
            dot={false}
          />
          <Line
            yAxisId="pbr"
            type="monotone"
            dataKey="pbr"
            name="PBR"
            stroke="var(--chart-3)"
            strokeWidth={2}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function ValuationTable({
  rows,
  locale,
  t,
}: {
  readonly rows: readonly ValuationRatiosRow[];
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("valuationTable.date")}</th>
            <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--chart-1)" }}>
              {t("valuationTable.per")}
            </th>
            <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--chart-3)" }}>
              {t("valuationTable.pbr")}
            </th>
            <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--chart-5)" }}>
              {t("valuationTable.dividendYield")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {rows.map((row) => (
            <tr key={row.trade_date}>
              <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">
                {row.trade_date}
              </td>
              <td
                className="px-2 py-1.5 text-right font-num tabular-nums"
                style={{ color: "var(--chart-1)" }}
              >
                {formatNumber(row.per, locale, { decimals: 2 })}
              </td>
              <td
                className="px-2 py-1.5 text-right font-num tabular-nums"
                style={{ color: "var(--chart-3)" }}
              >
                {formatNumber(row.pbr, locale, { decimals: 2 })}
              </td>
              <td
                className="px-2 py-1.5 text-right font-num tabular-nums"
                style={{ color: "var(--chart-5)" }}
              >
                {formatNumber(row.dividend_yield, locale, { decimals: 2 })}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 分析師深度 — 3 real sub-cards (rating distribution + target-price range, EPS/revenue estimate +
 * long-term-growth estimate, latest upgrade/downgrade), each carrying an "N analysts · as of
 * MM/DD" attribution line so a target price/rating is never presented as this app's own view
 * (web/CLAUDE.md §4 non-trading language rule). Hidden entirely — not an empty scaffold — when
 * `consensus` is null (yfinance tracks no analyst coverage for this symbol, common for small/mid
 * caps), matching the design doc's own "Coverage-gated → hidden when null" rule. */
/** Exported so `us-stock/overview-panel.tsx` can reuse this exact card — the US 總覽 tab folds in
 * the one genuinely non-TW-gated card this tab has (see `ResearchChartsPanel`'s own docstring),
 * rather than forking a second copy of the rating/target/estimate/upgrade-downgrade markup. */
export function AnalystConsensusCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.research");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["analyst_consensus", symbol, market],
    queryFn: () => getAnalystConsensus(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <CardSkeletonBody height={220} />
      </section>
    );
  }

  const consensus = query.data?.consensus;
  if (!consensus) return null;

  const attribution = t("attribution", {
    count: consensus.number_of_analysts,
    date: consensus.as_of,
  });

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">
        {t("analystConsensusTitle")}
      </h2>
      <p className="mb-4 text-[12px] text-[var(--text-muted)]">{attribution}</p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <RatingTargetCard consensus={consensus} locale={locale} t={t} />
        <EstimatesCard consensus={consensus} locale={locale} t={t} />
        <UpgradeDowngradeCard consensus={consensus} t={t} />
      </div>
    </section>
  );
}

const RATING_BUCKET_COLOR: Record<string, string> = {
  strong_buy: "var(--chart-1)",
  buy: "var(--chart-2)",
  hold: "var(--chart-3)",
  sell: "var(--chart-4)",
  strong_sell: "var(--chart-5)",
};

function RatingTargetCard({
  consensus,
  locale,
  t,
}: {
  readonly consensus: AnalystConsensus;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const buckets: { key: string; value: number }[] = [
    { key: "strong_buy", value: consensus.strong_buy ?? 0 },
    { key: "buy", value: consensus.buy ?? 0 },
    { key: "hold", value: consensus.hold ?? 0 },
    { key: "sell", value: consensus.sell ?? 0 },
    { key: "strong_sell", value: consensus.strong_sell ?? 0 },
  ];
  const total = buckets.reduce((sum, b) => sum + b.value, 0);

  return (
    <div
      className="flex flex-col gap-3 rounded-[var(--radius-input)] border p-3 backdrop-blur-md"
      style={{
        borderColor: "color-mix(in srgb, var(--chart-2) 35%, var(--border))",
        backgroundColor: "color-mix(in srgb, var(--chart-2) 10%, var(--surface-2) 60%)",
      }}
    >
      <p className="text-[12px]" style={{ color: "var(--chart-2)" }}>
        {t("ratingTargetTitle")}
      </p>
      {consensus.recommendation_key ? (
        <p
          className="font-semibold text-[15px] capitalize"
          style={{
            color: RATING_BUCKET_COLOR[consensus.recommendation_key] ?? "var(--text)",
          }}
        >
          {consensus.recommendation_key.replaceAll("_", " ")}
        </p>
      ) : null}
      {total > 0 ? (
        <div className="flex h-2 overflow-hidden rounded-[var(--radius-pill)]">
          {buckets
            .filter((b) => b.value > 0)
            .map((b) => (
              <div
                key={b.key}
                style={{
                  width: `${(b.value / total) * 100}%`,
                  backgroundColor: RATING_BUCKET_COLOR[b.key],
                }}
              />
            ))}
        </div>
      ) : null}
      {consensus.mean_target != null ? (
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] text-[var(--text-muted)]">{t("meanTargetLabel")}</span>
          <span className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(consensus.mean_target, locale, { decimals: 2 })}
          </span>
        </div>
      ) : null}
      {consensus.low_target != null && consensus.high_target != null ? (
        <p
          className="font-num text-[11px] tabular-nums"
          style={{ color: "color-mix(in srgb, var(--chart-2) 70%, var(--text-muted))" }}
        >
          {t("targetRangeLabel", {
            low: formatNumber(consensus.low_target, locale, { decimals: 2 }),
            high: formatNumber(consensus.high_target, locale, { decimals: 2 }),
          })}
        </p>
      ) : null}
    </div>
  );
}

function EstimatesCard({
  consensus,
  locale,
  t,
}: {
  readonly consensus: AnalystConsensus;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const hasAny =
    consensus.forward_eps_estimate != null ||
    consensus.forward_revenue_estimate != null ||
    consensus.long_term_growth_estimate != null ||
    consensus.current_year_growth_estimate != null ||
    consensus.next_year_growth_estimate != null;
  return (
    <div
      className="flex flex-col gap-3 rounded-[var(--radius-input)] border p-3 backdrop-blur-md"
      style={{
        borderColor: "color-mix(in srgb, var(--chart-4) 35%, var(--border))",
        backgroundColor: "color-mix(in srgb, var(--chart-4) 10%, var(--surface-2) 60%)",
      }}
    >
      <p className="text-[12px]" style={{ color: "var(--chart-4)" }}>
        {t("estimatesTitle")}
      </p>
      {!hasAny ? <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p> : null}
      {consensus.forward_eps_estimate != null ? (
        <EstimateRow
          label={t("forwardEpsLabel")}
          value={formatNumber(consensus.forward_eps_estimate, locale, { decimals: 2 })}
        />
      ) : null}
      {consensus.forward_revenue_estimate != null ? (
        <EstimateRow
          label={t("forwardRevenueLabel")}
          value={formatCompactNumber(consensus.forward_revenue_estimate, locale)}
        />
      ) : null}
      {consensus.current_year_growth_estimate != null ? (
        <EstimateRow
          label={t("currentYearGrowthLabel")}
          value={`${formatNumber(consensus.current_year_growth_estimate * 100, locale, { decimals: 1 })}%`}
        />
      ) : null}
      {consensus.next_year_growth_estimate != null ? (
        <EstimateRow
          label={t("nextYearGrowthLabel")}
          value={`${formatNumber(consensus.next_year_growth_estimate * 100, locale, { decimals: 1 })}%`}
        />
      ) : null}
      {consensus.long_term_growth_estimate != null ? (
        <EstimateRow
          label={t("longTermGrowthLabel")}
          value={`${formatNumber(consensus.long_term_growth_estimate * 100, locale, { decimals: 1 })}%`}
        />
      ) : null}
    </div>
  );
}

function EstimateRow({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}): ReactNode {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-[11px] text-[var(--text-muted)]">{label}</span>
      <span className="font-num text-[14px] text-[var(--text)] tabular-nums">{value}</span>
    </div>
  );
}

/** `latest_upgrade_downgrade_action`'s color — a 3-way sign lookup pulled out of
 * `UpgradeDowngradeCard` as a named function rather than a nested ternary. */
function upgradeDowngradeColor(action: string | null | undefined): string {
  if (action === "up") return "var(--up)";
  if (action === "down") return "var(--down)";
  return "var(--text-muted)";
}

function UpgradeDowngradeCard({
  consensus,
  t,
}: {
  readonly consensus: AnalystConsensus;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const action = consensus.latest_upgrade_downgrade_action;
  const firm = consensus.latest_upgrade_downgrade_firm;
  const color = upgradeDowngradeColor(action);
  return (
    <div
      className="flex flex-col gap-3 rounded-[var(--radius-input)] border p-3 backdrop-blur-md"
      style={{
        borderColor: "color-mix(in srgb, var(--chart-8) 35%, var(--border))",
        backgroundColor: "color-mix(in srgb, var(--chart-8) 10%, var(--surface-2) 60%)",
      }}
    >
      <p className="text-[12px]" style={{ color: "var(--chart-8)" }}>
        {t("upgradeDowngradeTitle")}
      </p>
      {firm && action ? (
        <>
          <p className="font-semibold text-[14px]" style={{ color }}>
            {t(`upgradeDowngradeAction.${action}` as "upgradeDowngradeAction.up")}
          </p>
          <p className="text-[13px] text-[var(--text)]">{firm}</p>
        </>
      ) : (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      )}
    </div>
  );
}

const STOCK_EVENT_TYPES_WITH_FEED_DATA = [
  "dividend_ex_rights",
  "cash_capital_increase_rights",
  "agm",
] as const;
const STOCK_EVENT_DOT_COLOR: Record<string, string> = {
  dividend_ex_rights: "var(--chart-1)",
  cash_capital_increase_rights: "var(--chart-3)",
  agm: "var(--chart-4)",
};

type StockEventsView = "list" | "calendar";

/** 個股事件 — a 清單/日曆 toggle over `get_calendar_events`'s exact same type-filtered feed
 * `basic-info-panel.tsx`'s own 公告/事件 card already consumes (same query key too, so TanStack
 * Query single-flights the fetch if both tabs are mounted with the same date window). 清單 is a
 * flat chronological `divide-y` list; 日曆 reuses the identical month-grid + colored-dot pattern
 * `AnnouncementsCalendarView` already established for 基本資料, so the two calendars in this app
 * look and behave the same rather than inventing a second visual language for the same shape of
 * data. */
function StockEventsCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.research");
  const [view, setView] = useState<StockEventsView>("list");
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({
    year: today.getFullYear(),
    month: today.getMonth() + 1,
  });
  const query = useQuery({
    queryKey: ["stock_calendar_events", symbol, market],
    queryFn: () =>
      getStockCalendarEvents(symbol, market, {
        startDate: daysFromTodayIso(-365),
        endDate: daysFromTodayIso(730),
      }),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <CardSkeletonBody height={260} />
      </section>
    );
  }
  const data = query.data;
  if (!data) return null;

  if (isMaskedTeaser(data)) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <SponsorTeaser
          headline={t("eventsTitle")}
          preview={data.preview}
          requiredTier={data.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </section>
    );
  }

  const events = [...data.events]
    .filter((e) =>
      STOCK_EVENT_TYPES_WITH_FEED_DATA.includes(
        e.type as (typeof STOCK_EVENT_TYPES_WITH_FEED_DATA)[number],
      ),
    )
    .sort((a, b) => (b.event_date ?? "").localeCompare(a.event_date ?? ""));

  let eventsBody: ReactNode;
  if (events.length === 0) {
    eventsBody = <p className="text-[13px] text-[var(--text-muted)]">{t("eventsEmpty")}</p>;
  } else if (view === "list") {
    eventsBody = <StockEventsTimeline events={events} />;
  } else {
    eventsBody = (
      <StockEventsCalendarView
        year={cursor.year}
        month={cursor.month}
        events={events}
        onPrevMonth={() =>
          setCursor(({ year, month }) =>
            month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 },
          )
        }
        onNextMonth={() =>
          setCursor(({ year, month }) =>
            month === 12 ? { year: year + 1, month: 1 } : { year, month: month + 1 },
          )
        }
      />
    );
  }

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("eventsTitle")}</h2>
        {events.length > 0 ? (
          <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
            <ViewToggleButton
              active={view === "list"}
              label={t("viewList")}
              onClick={() => setView("list")}
            />
            <ViewToggleButton
              active={view === "calendar"}
              label={t("viewCalendar")}
              onClick={() => setView("calendar")}
            />
          </div>
        ) : null}
      </div>
      {eventsBody}
    </section>
  );
}

function ViewToggleButton({
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
        "rounded-[var(--radius-pill)] px-3 py-1 text-[12px] transition-colors",
        active
          ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}

function StockEventsTimeline({
  events,
}: {
  readonly events: readonly CalendarEventOut[];
}): ReactNode {
  const t = useTranslations("stock.research");
  return (
    <ol className="divide-y divide-[var(--border)]">
      {events.map((event) => (
        <li key={event.id} className="flex items-center gap-3 py-2.5">
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: STOCK_EVENT_DOT_COLOR[event.type] ?? "var(--chart-6)" }}
            aria-hidden
          />
          <span className="flex-1 text-[13px] text-[var(--text)]">{event.title_zh}</span>
          <span
            className="rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]"
            style={{
              backgroundColor: `color-mix(in srgb, ${STOCK_EVENT_DOT_COLOR[event.type] ?? "var(--chart-6)"} 16%, transparent)`,
              color: STOCK_EVENT_DOT_COLOR[event.type] ?? "var(--chart-6)",
            }}
          >
            {t(`eventType.${event.type}` as "eventType.dividend_ex_rights")}
          </span>
          <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
            {event.event_date ?? "—"}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Two-column layout — the filtered event list sits to the LEFT of the shared `MonthGridCalendar`
 * grid (not stacked below it), per explicit design direction. The grid/dot/selection surface
 * itself is `MonthGridCalendar` (components/ui/), shared verbatim with basic-info-panel.tsx's
 * `AnnouncementsCalendarView` — this component only owns the events-specific dot-color mapping
 * and the two-column arrangement. */
function StockEventsCalendarView({
  year,
  month,
  events,
  onPrevMonth,
  onNextMonth,
}: {
  readonly year: number;
  readonly month: number;
  readonly events: readonly CalendarEventOut[];
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("stock.research");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const dotsByDay = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const e of events) {
      if (!e.event_date) continue;
      const colors = map.get(e.event_date) ?? [];
      colors.push(STOCK_EVENT_DOT_COLOR[e.type] ?? "var(--chart-6)");
      map.set(e.event_date, [...new Set(colors)]);
    }
    return map;
  }, [events]);

  const visibleEvents = selectedDay ? events.filter((e) => e.event_date === selectedDay) : events;

  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-start">
      <div className="min-w-0 md:w-64 md:shrink-0">
        {visibleEvents.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("eventsEmpty")}</p>
        ) : (
          <StockEventsTimeline events={visibleEvents} />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <MonthGridCalendar
          year={year}
          month={month}
          dotsByDay={dotsByDay}
          selectedDay={selectedDay}
          onSelectDay={setSelectedDay}
          onPrevMonth={onPrevMonth}
          onNextMonth={onNextMonth}
          prevMonthLabel={t("prevMonth")}
          nextMonthLabel={t("nextMonth")}
          monthTitle={t("monthTitle", { year, month })}
        />
      </div>
    </div>
  );
}
