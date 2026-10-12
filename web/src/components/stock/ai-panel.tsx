"use client";

import {
  LightbulbIcon,
  TrendDownIcon,
  TrendUpIcon,
  WarningIcon,
} from "@phosphor-icons/react/dist/ssr";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { GaugeCard } from "@/components/market/gauge-card";
import { SponsorGatedBody } from "@/components/market/sponsor-teaser";
import { getStockScore, getStockSummary, getTrendScore } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { SymbolType } from "@/types/market";
import type { Market } from "@/types/settings";
import type { TrendScoreDimension } from "@/types/stock-detail";

type ScoreProfile = "swing" | "position";
const PROFILES: readonly ScoreProfile[] = ["swing", "position"];

// Same 0-40/40-60/60-100 band cutoffs the 多空評分 gauge uses (`ScoreGaugeCard`'s own `bands`) —
// reused here so a score means the same color everywhere on this tab, never a second scale.
const BEARISH_MAX = 40;
const BULLISH_MIN = 60;

function bandColorFor(value: number): string {
  if (value < BEARISH_MAX) return "var(--down)";
  if (value >= BULLISH_MIN) return "var(--up)";
  return "var(--warn)";
}

// Two visual columns (matching the reference layout's split), each its own `--chart-N` token —
// never a signed 紅漲綠跌 color, since a dimension bar-length is a magnitude, not a price direction.
// US rows never carry a TW-only dimension name (chip_flow/margin_credit/broker_concentration/
// sector_rotation) — see `analytics/trend_score.py::US_DIMENSION_WEIGHTS`'s own comment — so a
// separate US column set is required rather than one shared list with unmatched names silently
// rendering nothing.
const TW_TREND_DIMENSION_COLUMNS = [
  { names: ["technical", "chip_flow", "margin_credit"] as const, color: "var(--chart-1)" },
  {
    names: ["broker_concentration", "sector_rotation", "taiex_correlation"] as const,
    color: "var(--chart-2)",
  },
] as const;
const US_TREND_DIMENSION_COLUMNS = [
  {
    names: ["technical", "institutional_ownership", "insider_sentiment"] as const,
    color: "var(--chart-1)",
  },
  {
    names: ["analyst_revision", "sector_momentum", "taiex_correlation"] as const,
    color: "var(--chart-2)",
  },
] as const;

function trendDimensionColumnsFor(market: Market) {
  return market === "US" ? US_TREND_DIMENSION_COLUMNS : TW_TREND_DIMENSION_COLUMNS;
}

/** 個股 › AI tab (renamed from the never-built "summary" placeholder) — three cards read
 * top-to-bottom as one story, per the agreed design: (1) 趨勢評分, a 6-dimension descriptive
 * trend-strength composite (public, deliberately never a forecast — see
 * `analytics/trend_score.py`'s own docstring for why a forward directional call has no place
 * here); (2) 多空評分, the existing cross-sectional multi-factor score gauge (public, its own
 * 籌碼 sub-signal degrades gracefully); (3) AI 智能摘要, an LLM-generated headline/narrative/SWOT
 * strictly grounded in (2)'s cached factor breakdown (Sponsor-tier, masked via the shared
 * `SponsorGatedBody`/`SponsorTeaser` pattern). A mandatory, non-maskable non-trading caveat
 * footer closes the tab regardless of any card's lock state. */
export function AiPanel({
  symbol,
  market,
  symbolType = "stock",
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly symbolType?: SymbolType;
}): ReactNode {
  const t = useTranslations("stock.ai");

  return (
    <div className="flex flex-col gap-4">
      <TrendScoreCard symbol={symbol} market={market} />
      <ScoreGaugeCard symbol={symbol} market={market} symbolType={symbolType} />
      <SummaryCard symbol={symbol} market={market} />
      <p className="text-[11px] text-[var(--text-muted)]">{t("footerCaveat")}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 趨勢評分 — get_trend_score (public; broker_concentration sub-dimension locks for Free)
// ---------------------------------------------------------------------------

function TrendScoreCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.ai");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["trend_score", symbol, market],
    queryFn: () => getTrendScore(symbol, market),
  });
  const data = query.data;
  const history = data?.history ?? [];
  const chartData = history
    .filter((point) => point.composite !== null)
    .map((point) => ({
      date: point.trade_date,
      composite: point.composite as number,
      close: point.close,
    }));
  const hasCloseSeries = chartData.some((point) => point.close !== null);

  if (query.isLoading) return <CardSkeletonBody height={420} />;
  if (!data) return null;

  return (
    <section className="flex flex-col surface-card p-4 sm:p-5">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("trendScoreTitle")}</h2>
        {data.as_of ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("asOfDate", { date: data.as_of })}
          </span>
        ) : null}
      </div>

      {!data.found ? (
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">
          {data.message || t("trendScoreUnavailable")}
        </p>
      ) : (
        <>
          <div
            className="my-2 font-num text-[28px] tabular-nums"
            style={{
              color: data.composite !== null ? bandColorFor(data.composite) : "var(--text)",
            }}
          >
            {data.composite !== null ? (
              <>
                {formatNumber(data.composite, locale, { decimals: 1 })}
                <span className="ml-1 text-[14px] text-[var(--text-muted)]">{t("scoreUnit")}</span>
              </>
            ) : (
              "—"
            )}
          </div>
          {chartData.length > 0 ? (
            <div className="h-[200px] w-full">
              <ResponsiveContainer>
                <ComposedChart
                  data={chartData}
                  margin={{ left: 8, right: hasCloseSeries ? 8 : 24 }}
                >
                  <CartesianGrid stroke="var(--grid)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    stroke="var(--border)"
                  />
                  <YAxis
                    yAxisId="score"
                    domain={[0, 100]}
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    width={32}
                    stroke="var(--border)"
                  />
                  {hasCloseSeries ? (
                    <YAxis
                      yAxisId="price"
                      orientation="right"
                      domain={["auto", "auto"]}
                      tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                      width={44}
                      stroke="var(--border)"
                    />
                  ) : null}
                  {/* Dotted reference lines at the same 40/60 band cutoffs the big number and
                   * every bar are colored against — so a viewer can read a bar's zone from the
                   * chart alone, without hovering for the tooltip. */}
                  <ReferenceLine
                    yAxisId="score"
                    y={BEARISH_MAX}
                    stroke="var(--down)"
                    strokeDasharray="4 4"
                    strokeOpacity={0.5}
                  />
                  <ReferenceLine
                    yAxisId="score"
                    y={BULLISH_MIN}
                    stroke="var(--up)"
                    strokeDasharray="4 4"
                    strokeOpacity={0.5}
                  />
                  <Tooltip
                    cursor={{ fill: "var(--text)", opacity: 0.08 }}
                    contentStyle={{
                      background: "var(--surface-2)",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-input)",
                      color: "var(--text)",
                    }}
                    labelStyle={{ color: "var(--text)", fontWeight: 600 }}
                    itemStyle={{ color: "var(--text)" }}
                    formatter={(value, name) =>
                      name === "close"
                        ? [
                            formatNumber(value as number, locale, { decimals: 2 }),
                            t("priceSeriesLabel"),
                          ]
                        : [
                            `${formatNumber(value as number, locale, { decimals: 1 })} ${t("scoreUnit")}`,
                            t("trendScoreSeriesLabel"),
                          ]
                    }
                  />
                  <Bar yAxisId="score" dataKey="composite" radius={2}>
                    {chartData.map((point) => (
                      <Cell key={point.date} fill={bandColorFor(point.composite)} />
                    ))}
                  </Bar>
                  {hasCloseSeries ? (
                    <Line
                      yAxisId="price"
                      type="monotone"
                      dataKey="close"
                      name="close"
                      stroke="var(--text)"
                      strokeWidth={1.5}
                      dot={false}
                      connectNulls
                    />
                  ) : null}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="text-[13px] text-[var(--text-muted)]">{t("noHistoryYet")}</p>
          )}
          <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-4 border-[var(--border)] border-t pt-4 sm:grid-cols-2">
            {trendDimensionColumnsFor(market).map((column) => (
              <div key={column.color} className="flex flex-col gap-4">
                {column.names.map((name) => {
                  const dimension = data.dimensions.find((d) => d.name === name);
                  if (!dimension) return null;
                  // `broker_concentration` renders a Sponsor-lock teaser even with `value: null`
                  // (a real product feature, not a missing dimension) — every other dimension
                  // the batch never computed for this row is skipped rather than shown as a bare
                  // "no data" placeholder.
                  const isLockedTeaser = name === "broker_concentration";
                  if (dimension.value === null && !isLockedTeaser) return null;
                  return (
                    <TrendDimensionRow
                      key={name}
                      dimension={dimension}
                      locale={locale}
                      color={column.color}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function TrendDimensionRow({
  dimension,
  locale,
  color,
}: {
  readonly dimension: TrendScoreDimension;
  readonly locale: string;
  readonly color: string;
}): ReactNode {
  const t = useTranslations("stock.ai");
  // Locked only when the API says the tier gates it; a null value on its own just means no data
  // yet (e.g. 分點 not ingested), which must not read as an upgrade prompt to a Pro user.
  const locked = dimension.locked;
  const hasValue = dimension.value !== null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] text-[var(--text)]">
          {t(`dimension.${dimension.name}` as const)}
        </span>
        {hasValue ? (
          <span className="shrink-0 font-num text-[13px] tabular-nums" style={{ color }}>
            {formatNumber(dimension.value as number, locale, { decimals: 0 })}
          </span>
        ) : null}
      </div>
      <div
        className={cn(
          "h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]",
          locked && "border border-[var(--border)] border-dashed",
        )}
      >
        {hasValue ? (
          <div
            className="h-full rounded-full"
            style={{ width: `${dimension.value}%`, backgroundColor: color }}
          />
        ) : null}
      </div>
      <p className="text-[11px] text-[var(--text-muted)] leading-[1.5]">
        {locked
          ? t("dimensionLocked")
          : hasValue
            ? t(`dimensionCaption.${dimension.name}` as const)
            : t("dimensionUnavailable")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 多空評分 — get_stock_score (public; existing cross-sectional multi-factor score)
// ---------------------------------------------------------------------------

const STOCK_FACTOR_ORDER = [
  "momentum",
  "trend_quality",
  "value_guard",
  "quality",
  "revenue_flow",
] as const;

// `Market.US`'s `UsStockScoreBreakdown` swaps the TW-only `revenue_flow` for `analyst_revision`
// (see that model's own docstring) — a US row never carries `revenue_flow`, so it needs its own
// factor-order list rather than sharing `STOCK_FACTOR_ORDER`.
const US_STOCK_FACTOR_ORDER = [
  "momentum",
  "trend_quality",
  "value_guard",
  "quality",
  "analyst_revision",
  "growth",
  "liquidity",
] as const;

// An ETF's `ScoreResult.breakdown` carries an entirely different factor set than a stock's
// (`analytics/score.py`'s `EtfScoreBreakdown` vs `StockScoreBreakdown`) — only `revenue_flow` is
// shared. Iterating `STOCK_FACTOR_ORDER` against an ETF result would silently render 4 of 5 rows
// blank (every stock-only key is `undefined`), so the order must branch by symbol type.
const ETF_FACTOR_ORDER = [
  "premium_discount",
  "cost",
  "management_activity",
  "liquidity_flow",
  "revenue_flow",
] as const;
type FactorName =
  | (typeof STOCK_FACTOR_ORDER)[number]
  | (typeof US_STOCK_FACTOR_ORDER)[number]
  | (typeof ETF_FACTOR_ORDER)[number];

function factorOrderFor(symbolType: SymbolType, market: Market): readonly FactorName[] {
  if (symbolType !== "stock") return ETF_FACTOR_ORDER;
  return market === "US" ? US_STOCK_FACTOR_ORDER : STOCK_FACTOR_ORDER;
}

function ScoreGaugeCard({
  symbol,
  market,
  symbolType,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly symbolType: SymbolType;
}): ReactNode {
  const t = useTranslations("stock.ai");
  const locale = useLocale();
  const [profile, setProfile] = useState<ScoreProfile>("swing");
  const query = useQuery({
    queryKey: ["stock_score", symbol, market, profile],
    queryFn: () => getStockScore(symbol, market, profile),
  });
  const data = query.data;

  if (query.isLoading) return <CardSkeletonBody height={320} />;
  if (!data) return null;

  const result = data.result;
  const bands = [
    { min: 0, max: 40, label: t("band.bearish"), color: "var(--down)" },
    { min: 40, max: 60, label: t("band.neutral"), color: "var(--warn)" },
    { min: 60, max: Number.POSITIVE_INFINITY, label: t("band.bullish"), color: "var(--up)" },
  ];

  return (
    <section className="flex flex-col surface-card p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("scoreTitle")}</h2>
        <div
          role="tablist"
          aria-label={t("profileToggleLabel")}
          className="flex w-fit gap-1 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface-2)] p-1"
        >
          {PROFILES.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={profile === key}
              onClick={() => setProfile(key)}
              className={cn(
                "min-h-8 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
                profile === key
                  ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(`profile.${key}` as const)}
            </button>
          ))}
        </div>
      </div>

      {!data.found || !result ? (
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">
          {data.message || t("scoreUnavailable")}
        </p>
      ) : (
        <>
          <div className="flex justify-center">
            <GaugeCard
              title={t("scoreTitle")}
              value={result.composite}
              domainMin={0}
              domainMax={100}
              bands={bands}
              leftAxisLabel={t("band.bearish")}
              rightAxisLabel={t("band.bullish")}
            />
          </div>
          <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-4 border-[var(--border)] border-t pt-4 sm:grid-cols-2">
            {factorOrderFor(symbolType, market).map((name) => {
              const factor = result.breakdown?.[name];
              // A factor absent from this row's breakdown shape (e.g. `revenue_flow` on a US
              // row, `analyst_revision` on a TW row) — or unavailable for this run — is never
              // rendered as an empty placeholder row, only factors that resolved to a real value.
              return factor && factor.percentile !== null ? (
                <FactorRow key={name} name={name} factor={factor} locale={locale} />
              ) : null;
            })}
          </div>
        </>
      )}
    </section>
  );
}

function FactorRow({
  name,
  factor,
  locale,
}: {
  readonly name: FactorName;
  readonly factor: {
    percentile: number | null;
    description: string;
    unavailable_reason?: string | null;
  };
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.ai");
  const hasValue = factor.percentile !== null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] text-[var(--text)]">{t(`factor.${name}` as const)}</span>
        {hasValue ? (
          <span className="shrink-0 font-num text-[13px] text-[var(--accent)] tabular-nums">
            {formatNumber(factor.percentile as number, locale, { decimals: 0 })}
          </span>
        ) : null}
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
        {hasValue ? (
          <div
            className="h-full rounded-full bg-[var(--accent)]"
            style={{ width: `${factor.percentile}%` }}
          />
        ) : null}
      </div>
      <p className="text-[11px] text-[var(--text-muted)] leading-[1.5]">
        {hasValue
          ? t(`factorCaption.${name}` as const)
          : (factor.unavailable_reason ?? t("dimensionUnavailable"))}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI 智能摘要 — get_stock_summary (Sponsor-tier; masked via the shared teaser pattern)
// ---------------------------------------------------------------------------

// Each SWOT quadrant gets its own icon + tint so the 2x2 grid reads at a glance, never
// color-alone (an icon carries the same favorable/unfavorable signal as the tint) — and never
// `--up`/`--down` (reserved for signed price/score direction per web/CLAUDE.md §4). 優勢/機會 are
// the favorable-reading quadrants (tinted `--accent`); 劣勢/威脅 are the caution quadrants (tinted
// `--warn`) — a plain descriptive framing, not a bullish/bearish call.
const SWOT_QUADRANTS = [
  { key: "strengths", icon: TrendUpIcon, tint: "var(--accent)" },
  { key: "weaknesses", icon: TrendDownIcon, tint: "var(--warn)" },
  { key: "opportunities", icon: LightbulbIcon, tint: "var(--accent)" },
  { key: "threats", icon: WarningIcon, tint: "var(--warn)" },
] as const;

function SummaryCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.ai");
  const query = useQuery({
    queryKey: ["stock_summary", symbol, market],
    queryFn: () => getStockSummary(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={280} />;
  if (!query.data) return null;

  return (
    <section className="surface-card p-4 sm:p-5">
      <SponsorGatedBody
        data={
          "locked" in query.data ? query.data : { ...query.data, data_available: query.data.found }
        }
        title={t("summaryTitle")}
        unavailableCopy={t("summaryUnavailable")}
      >
        {(data) => (
          <>
            <h2 className="mb-2 font-semibold text-[15px] text-[var(--text)]">
              {t("summaryTitle")}
            </h2>
            <p className="mb-4 text-[14px] text-[var(--text)] leading-[1.7]">{data.headline}</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {SWOT_QUADRANTS.map(({ key, icon: QuadrantIcon, tint }) => (
                <div
                  key={key}
                  className="rounded-[var(--radius-input)] border p-3"
                  style={{
                    borderColor: `color-mix(in srgb, ${tint} 35%, var(--border))`,
                    backgroundColor: `color-mix(in srgb, ${tint} 6%, var(--surface-2))`,
                  }}
                >
                  <h3 className="mb-1.5 flex items-center gap-1.5 font-medium text-[12px]">
                    <QuadrantIcon size={14} weight="regular" aria-hidden style={{ color: tint }} />
                    <span style={{ color: tint }}>{t(`swot.${key}` as const)}</span>
                  </h3>
                  {data.swot[key].length === 0 ? (
                    <p className="text-[12px] text-[var(--text-muted)]">{t("swotEmpty")}</p>
                  ) : (
                    <ul className="flex flex-col gap-1">
                      {data.swot[key].map((bullet) => (
                        <li key={bullet} className="text-[12.5px] text-[var(--text)] leading-[1.6]">
                          {bullet}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
            {data.narrative ? (
              <p className="mt-4 text-[13px] text-[var(--text)] leading-[1.7]">{data.narrative}</p>
            ) : null}
          </>
        )}
      </SponsorGatedBody>
    </section>
  );
}
