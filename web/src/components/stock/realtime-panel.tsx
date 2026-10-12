"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BaselineSeries,
  CandlestickSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import {
  getIntradayKbar,
  getLiveQuote,
  getPriceLimit,
  getPriceTick,
  getStockDayTrading,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { resolveToken } from "@/lib/resolve-css-token";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

// TWSE MIS is a single-shot snapshot (never a poll/stream) — the client re-fetches on this
// interval to approximate a live feel, matching the ~5s cadence the reference app's own ladder
// updates at. Off-hours the same snapshot keeps coming back, which is honest (no fabricated
// motion) — the panel just doesn't flash if nothing changed.
const LIVE_QUOTE_POLL_MS = 5_000;
const TICK_COLLAPSED_ROW_COUNT = 20;

/** 即時 tab — 分時 line chart + 逐筆 list (Sponsor, EOD replay) on top; 五檔 ladder (public) +
 * 分K candlestick chart (Sponsor) below; 當沖 stats full-width at the bottom. Each section's data
 * source is documented where it is fetched — no fabricated real-time surface.
 *
 * `get_intraday_kbar`/`get_price_tick` are single-trading-day-per-request upstream and default
 * to "today" when no date is named — on a weekend/holiday that's zero rows. `get_day_trading`
 * (public, already unmasked) is fetched once here and its latest row's `trade_date` is passed
 * down as the explicit date for both, so those two sections show the last real trading day's
 * data instead of a spurious empty state. */
export function RealtimeTab({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const dayTradingQuery = useQuery({
    queryKey: ["stock_day_trading", symbol, market],
    queryFn: () => getStockDayTrading(symbol, market),
  });
  const dayTradingRows = dayTradingQuery.data?.rows ?? [];
  const latestDayTrading = [...dayTradingRows]
    .sort((a, b) => b.trade_date.localeCompare(a.trade_date))
    .at(0);
  const latestTradeDate = latestDayTrading?.trade_date;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-2">
        <IntradayLineChart symbol={symbol} market={market} tradeDate={latestTradeDate} />
        <PriceTickList symbol={symbol} market={market} tradeDate={latestTradeDate} />
      </div>
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-2">
        <LiveQuoteLadder symbol={symbol} market={market} />
        <IntradayKbarChart symbol={symbol} market={market} tradeDate={latestTradeDate} />
      </div>
      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-2">
        <DayTradingRatioBar latest={latestDayTrading} isLoading={dayTradingQuery.isLoading} />
        <DetailStatsPanel symbol={symbol} market={market} tradeDate={latestTradeDate} />
      </div>
      <p className="text-[11px] text-[var(--text-muted)]">{t("footerNote")}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 五檔 ladder — get_live_quote_snapshot (public, TWSE MIS)
// ---------------------------------------------------------------------------

/** lightweight-charts' `Time` type accepts a bare `yyyy-mm-dd` string, a `BusinessDay` object, or
 * a UTC seconds timestamp — never a `yyyy-mm-ddTHH:mm[:ss]` datetime string, which throws at
 * render ("Invalid date string ... expected format=yyyy-mm-dd"). `trade_date`+`minute` need the
 * timestamp form to keep intraday minute resolution. `minute` is confirmed live to already
 * include seconds (`"09:00:00"`, not `"09:00"`) — appending `:00` unconditionally would produce
 * an invalid `T09:00:00:00Z` string that silently parses to `NaN`. Parsed as UTC so the chart's
 * own x-axis (which renders in UTC by default) doesn't shift the displayed time against the
 * TW-local `minute` string the API actually returned. */
function toChartTime(tradeDate: string, minute: string): UTCTimestamp {
  return (Date.parse(`${tradeDate}T${minute}Z`) / 1000) as UTCTimestamp;
}

/** `best_bid`/`best_ask`/`best_bid_volume`/`best_ask_volume` are `_`-delimited 5-tier strings
 * straight from TWSE MIS's own wire format (see `sources/twse_mis.py`'s docstring) — split
 * client-side into up to 5 `(price, volume)` pairs, padding a short/empty tier with `null` so a
 * partial depth (e.g. after-hours) still renders 5 fixed row slots rather than a jagged list. */
function parseLadderTiers(
  prices: string | null | undefined,
  volumes: string | null | undefined,
): readonly { price: number | null; volume: number | null }[] {
  const priceParts = (prices ?? "").split("_").filter((p) => p !== "");
  const volumeParts = (volumes ?? "").split("_").filter((v) => v !== "");
  return Array.from({ length: 5 }, (_, i) => {
    const price = priceParts[i];
    const volume = volumeParts[i];
    return {
      price: price !== undefined && price !== "-" ? Number(price) : null,
      volume: volume !== undefined && volume !== "-" ? Number(volume) : null,
    };
  });
}

function LiveQuoteLadder({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["stock_live_quote", symbol, market],
    queryFn: () => getLiveQuote(symbol, market),
    refetchInterval: LIVE_QUOTE_POLL_MS,
  });

  if (query.isLoading) return <CardSkeletonBody height={260} />;
  const snapshot = query.data?.snapshot;
  if (!snapshot) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("ladderTitle")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("ladderUnavailable")}</p>
      </section>
    );
  }

  const bids = parseLadderTiers(snapshot.best_bid, snapshot.best_bid_volume);
  const asks = parseLadderTiers(snapshot.best_ask, snapshot.best_ask_volume);
  const maxVolume = Math.max(
    1,
    ...bids.map((t2) => t2.volume ?? 0),
    ...asks.map((t2) => t2.volume ?? 0),
  );

  return (
    <section className="surface-card p-4 sm:p-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("ladderTitle")}</h2>
        <span className="font-num text-[18px] text-[var(--text)] tabular-nums">
          {formatNumber(snapshot.last_price, locale, { decimals: 2 })}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <LadderColumn label={t("bid")} tiers={bids} maxVolume={maxVolume} color="var(--up)" />
        <LadderColumn label={t("ask")} tiers={asks} maxVolume={maxVolume} color="var(--down)" />
      </div>
      {snapshot.observed_at ? (
        <p className="mt-3 text-[11px] text-[var(--text-muted)]">
          {t("observedAt", { time: snapshot.observed_at })}
        </p>
      ) : null}
    </section>
  );
}

function LadderColumn({
  label,
  tiers,
  maxVolume,
  color,
}: {
  readonly label: string;
  readonly tiers: readonly { price: number | null; volume: number | null }[];
  readonly maxVolume: number;
  readonly color: string;
}): ReactNode {
  const locale = useLocale();
  return (
    <div>
      <p className="mb-1.5 text-[11px] text-[var(--text-muted)]">{label}</p>
      <ul className="divide-y divide-[var(--border)]">
        {tiers.map((tier, index) => {
          const barWidth =
            tier.volume !== null ? Math.min((tier.volume / maxVolume) * 100, 100) : 0;
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: index is the ladder tier's own fixed identity (best-1..best-5).
            <li key={index} className="relative flex items-center justify-between px-1.5 py-1.5">
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 opacity-15"
                style={{ width: `${barWidth}%`, backgroundColor: color }}
              />
              <span
                className="relative z-10 font-num text-[13px] tabular-nums"
                style={{ color: tier.price !== null ? color : "var(--text-muted)" }}
              >
                {tier.price !== null ? formatNumber(tier.price, locale, { decimals: 2 }) : "—"}
              </span>
              <span className="relative z-10 font-num text-[12px] text-[var(--text)] tabular-nums">
                {tier.volume !== null ? formatNumber(tier.volume, locale, { decimals: 0 }) : "—"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 分時 line chart — get_intraday_kbar (Sponsor-tier), rendered as a price line vs. prior
// close + a volume histogram, matching the reference app's own default intraday view (a
// different, more common chart shape than the 分K candlesticks below).
// ---------------------------------------------------------------------------

function IntradayLineChart({
  symbol,
  market,
  tradeDate,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly tradeDate: string | undefined;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["stock_intraday_kbar", symbol, market, tradeDate, "line"],
    queryFn: () => getIntradayKbar(symbol, market, { startDate: tradeDate, endDate: tradeDate }),
    enabled: tradeDate !== undefined,
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  const rows = data !== undefined && !isMaskedTeaser(data) ? data.rows : [];
  const priorClose = rows[0]?.open;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || rows.length === 0 || priorClose === undefined) return;
    const chart: IChartApi = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: resolveToken("var(--text-muted)"),
      },
      grid: {
        horzLines: { color: resolveToken("var(--grid)") },
        vertLines: { color: resolveToken("var(--grid)") },
      },
      timeScale: { borderColor: resolveToken("var(--border)") },
      rightPriceScale: { borderColor: resolveToken("var(--border)") },
      crosshair: {
        vertLine: { color: resolveToken("var(--crosshair)") },
        horzLine: { color: resolveToken("var(--crosshair)") },
      },
      autoSize: true,
    });
    const lineSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: priorClose },
      topLineColor: resolveToken("var(--up)"),
      topFillColor1: resolveToken("color-mix(in srgb, var(--up) 28%, transparent)"),
      topFillColor2: resolveToken("color-mix(in srgb, var(--up) 4%, transparent)"),
      bottomLineColor: resolveToken("var(--down)"),
      bottomFillColor1: resolveToken("color-mix(in srgb, var(--down) 4%, transparent)"),
      bottomFillColor2: resolveToken("color-mix(in srgb, var(--down) 28%, transparent)"),
      lineWidth: 2,
    });
    lineSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.55 } });
    lineSeries.setData(
      rows.map((r) => ({ time: toChartTime(r.trade_date, r.minute), value: r.close })),
    );
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: resolveToken("color-mix(in srgb, var(--text-muted) 50%, transparent)"),
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.55, bottom: 0 } });
    volumeSeries.setData(
      rows.map((r) => ({
        time: toChartTime(r.trade_date, r.minute),
        value: r.volume,
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows, priorClose]);

  if (query.isLoading || tradeDate === undefined) return <CardSkeletonBody height={480} />;
  if (!data) return null;

  return (
    <section className="flex flex-col surface-card p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("lineChartTitle")}</h2>
      {locked && isMaskedTeaser(data) ? (
        <SponsorTeaser
          headline={t("lineChartTitle")}
          preview={data.preview}
          requiredTier={data.required_tier}
        >
          <div className="h-[440px] rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      ) : rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("kbarUnavailable")}</p>
      ) : (
        <div ref={containerRef} className="h-[440px] w-full flex-1" />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 分K candlestick chart — get_intraday_kbar (Sponsor-tier)
// ---------------------------------------------------------------------------

function IntradayKbarChart({
  symbol,
  market,
  tradeDate,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly tradeDate: string | undefined;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["stock_intraday_kbar", symbol, market, tradeDate, "candle"],
    queryFn: () => getIntradayKbar(symbol, market, { startDate: tradeDate, endDate: tradeDate }),
    enabled: tradeDate !== undefined,
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  const rows = data !== undefined && !isMaskedTeaser(data) ? data.rows : [];

  useEffect(() => {
    const container = containerRef.current;
    if (!container || rows.length === 0) return;
    const chart: IChartApi = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: resolveToken("var(--text-muted)"),
      },
      grid: {
        horzLines: { color: resolveToken("var(--grid)") },
        vertLines: { color: resolveToken("var(--grid)") },
      },
      timeScale: { borderColor: resolveToken("var(--border)") },
      rightPriceScale: { borderColor: resolveToken("var(--border)") },
      crosshair: {
        vertLine: { color: resolveToken("var(--crosshair)") },
        horzLine: { color: resolveToken("var(--crosshair)") },
      },
      autoSize: true,
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: resolveToken("var(--up)"),
      downColor: resolveToken("var(--down)"),
      borderVisible: false,
      wickUpColor: resolveToken("var(--up)"),
      wickDownColor: resolveToken("var(--down)"),
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.3 } });
    candleSeries.setData(
      rows.map((r) => ({
        time: toChartTime(r.trade_date, r.minute),
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      })),
    );
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });
    volumeSeries.setData(
      rows.map((r) => ({
        time: toChartTime(r.trade_date, r.minute),
        value: r.volume,
        color:
          r.close >= r.open
            ? resolveToken("color-mix(in srgb, var(--up) 50%, transparent)")
            : resolveToken("color-mix(in srgb, var(--down) 50%, transparent)"),
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rows]);

  if (query.isLoading || tradeDate === undefined) return <CardSkeletonBody height={360} />;
  if (!data) return null;

  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("kbarTitle")}</h2>
      {locked && isMaskedTeaser(data) ? (
        <SponsorTeaser
          headline={t("kbarTitle")}
          preview={data.preview}
          requiredTier={data.required_tier}
        >
          <div className="h-[320px] rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      ) : rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("kbarUnavailable")}</p>
      ) : (
        <div ref={containerRef} className="h-[320px] w-full" />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 當沖 — get_day_trading (public), already unmasked elsewhere on this page. `latest`/
// `isLoading` are passed down from `RealtimeTab`'s own fetch (also the source of the
// `tradeDate` the 分K/逐筆 sections resolve their weekend/holiday fallback against), rather
// than this component re-fetching the same data a second time.
// ---------------------------------------------------------------------------

function DayTradingRatioBar({
  latest,
  isLoading,
}: {
  readonly latest:
    | { trade_date: string; volume: number; buy_amount: number; sell_amount: number }
    | undefined;
  readonly isLoading: boolean;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const locale = useLocale();

  if (isLoading) return <CardSkeletonBody height={100} />;
  if (!latest) return null;

  // `get_day_trading`'s own `volume` field IS the day-trading volume, not total market volume —
  // 當沖率 (day-trade % of THAT day's own total volume) needs the day's whole-market volume,
  // which this tool doesn't carry. Rendering the raw day-trading volume alongside its own
  // buy/sell value is honest; a % against a volume this tool doesn't have would be fabricated.
  // Buy/sell amounts are raw TWD; `formatCompactNumber` renders them as "83.8億" style rather
  // than a wall of digits, matching every other large-TWD-amount stat elsewhere on this page.
  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-2 font-semibold text-[15px] text-[var(--text)]">{t("dayTradingTitle")}</h2>
      <div className="grid grid-cols-3 gap-3 text-[13px]">
        <Stat
          label={t("dayTradingVolume")}
          value={t("dayTradingVolumeUnit", {
            value: formatNumber(latest.volume, locale, { decimals: 0 }),
          })}
        />
        <Stat
          label={t("dayTradingBuyAmount")}
          value={t("dayTradingAmountUnit", {
            value: formatCompactNumber(latest.buy_amount, locale),
          })}
        />
        <Stat
          label={t("dayTradingSellAmount")}
          value={t("dayTradingAmountUnit", {
            value: formatCompactNumber(latest.sell_amount, locale),
          })}
        />
      </div>
      <p className="mt-2 text-[11px] text-[var(--text-muted)]">
        {t("dayTradingAsOf", { date: latest.trade_date })}
      </p>
    </section>
  );
}

function Stat({ label, value }: { readonly label: string; readonly value: string }): ReactNode {
  return (
    <div>
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p className="font-num text-[15px] text-[var(--text)] tabular-nums">{value}</p>
    </div>
  );
}

/** 漲跌 is a signed price delta, not a neutral stat — 紅漲綠跌 + ▲▼ + sign + `aria-label`
 * carrying direction/value (web/CLAUDE.md §4), matching `AssetHeader`'s own change display. */
function ChangeStat({
  label,
  change,
  changePct,
  locale,
}: {
  readonly label: string;
  readonly change: number | null;
  readonly changePct: number | null;
  readonly locale: string;
}): ReactNode {
  if (change === null || changePct === null) {
    return <Stat label={label} value="—" />;
  }
  const isUp = change >= 0;
  const color = isUp ? "var(--up)" : "var(--down)";
  return (
    <div>
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p
        role="group"
        aria-label={`${label} ${isUp ? "上漲" : "下跌"} ${formatNumber(Math.abs(change), locale, { decimals: 2 })} (${Math.abs(changePct).toFixed(2)}%)`}
        className="font-num text-[15px] tabular-nums"
        style={{ color }}
      >
        <span aria-hidden>
          {isUp ? "▲" : "▼"}
          {formatNumber(Math.abs(change), locale, { decimals: 2 })} (
          {Math.abs(changePct).toFixed(2)}%)
        </span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 詳細 — a fuller stat grid than 當沖's own 3 numbers. Sources, honestly split:
//   - 成交/最低/最高/總量 — direct fields off `get_live_quote_snapshot` (already polled by
//     `LiveQuoteLadder`; shares that query's cache key rather than re-fetching).
//   - 漲跌/幅度/振幅 — client-computed from that live snapshot joined with
//     `get_price_limit`'s `reference_price` (both real fields; the arithmetic itself, not the
//     inputs, is ours).
//   - 跌停/參考/漲停 — `get_price_limit`'s own fields, rendered as the reference app's
//     always-lit solid-fill pills (never conditional on the live price actually sitting there).
//   - 內盤/外盤/單量/均價 — aggregated client-side over the day's full `get_price_tick` replay
//     (內盤=tick_type "2" seller-initiated-at-bid, 外盤=tick_type "1" buyer-initiated-at-ask;
//     單量 is the most recent tick's own volume; 均價 is volume-weighted mean deal_price).
//     These four are EOD-replay derived like the rest of 逐筆成交 — never a live figure, even
//     though the source stats are real, not fabricated.
// ---------------------------------------------------------------------------

function DetailStatsPanel({
  symbol,
  market,
  tradeDate,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly tradeDate: string | undefined;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const locale = useLocale();
  const limitQuery = useQuery({
    queryKey: ["stock_price_limit", symbol, market, tradeDate],
    queryFn: () => getPriceLimit(symbol, market, { startDate: tradeDate, endDate: tradeDate }),
    enabled: tradeDate !== undefined,
  });
  const quoteQuery = useQuery({
    queryKey: ["stock_live_quote", symbol, market],
    queryFn: () => getLiveQuote(symbol, market),
    refetchInterval: LIVE_QUOTE_POLL_MS,
  });
  const tickQuery = useQuery({
    queryKey: ["stock_price_tick", symbol, market, tradeDate],
    queryFn: () => getPriceTick(symbol, market, { startDate: tradeDate, endDate: tradeDate }),
    enabled: tradeDate !== undefined,
  });

  if (limitQuery.isLoading || tradeDate === undefined) return <CardSkeletonBody height={220} />;
  const limitRow = limitQuery.data?.rows.at(0);
  if (!limitRow) return null;

  const snapshot = quoteQuery.data?.snapshot;
  const change = snapshot ? snapshot.last_price - limitRow.reference_price : null;
  const changePct =
    change !== null && limitRow.reference_price !== 0
      ? (change / limitRow.reference_price) * 100
      : null;
  const amplitudePct =
    snapshot && limitRow.reference_price !== 0
      ? ((snapshot.high - snapshot.low) / limitRow.reference_price) * 100
      : null;

  const tickData = tickQuery.data;
  const tickRows = tickData !== undefined && !isMaskedTeaser(tickData) ? tickData.rows : [];
  const insideVolume = tickRows
    .filter((r) => r.tick_type === "2")
    .reduce((sum, r) => sum + r.volume, 0);
  const outsideVolume = tickRows
    .filter((r) => r.tick_type === "1")
    .reduce((sum, r) => sum + r.volume, 0);
  const totalTickVolume = tickRows.reduce((sum, r) => sum + r.volume, 0);
  const avgPrice =
    totalTickVolume > 0
      ? tickRows.reduce((sum, r) => sum + r.deal_price * r.volume, 0) / totalTickVolume
      : null;
  const lastTradeSize = tickRows.at(-1)?.volume ?? null;

  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-2 font-semibold text-[15px] text-[var(--text)]">{t("detailTitle")}</h2>
      <div className="grid grid-cols-3 gap-3 text-[13px]">
        <LimitPricePill
          label={t("limitDown")}
          value={formatNumber(limitRow.limit_down, locale, { decimals: 2 })}
          color="var(--down)"
        />
        <LimitPricePill
          label={t("limitUp")}
          value={formatNumber(limitRow.limit_up, locale, { decimals: 2 })}
          color="var(--up)"
        />
        <LimitPricePill
          label={t("referencePrice")}
          value={formatNumber(limitRow.reference_price, locale, { decimals: 2 })}
          color="var(--warn)"
        />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3 text-[13px]">
        <Stat
          label={t("lastPrice")}
          value={snapshot ? formatNumber(snapshot.last_price, locale, { decimals: 2 }) : "—"}
        />
        <ChangeStat label={t("change")} change={change} changePct={changePct} locale={locale} />
        <Stat
          label={t("amplitude")}
          value={amplitudePct !== null ? `${amplitudePct.toFixed(2)}%` : "—"}
        />
        <Stat
          label={t("low")}
          value={snapshot ? formatNumber(snapshot.low, locale, { decimals: 2 }) : "—"}
        />
        <Stat
          label={t("high")}
          value={snapshot ? formatNumber(snapshot.high, locale, { decimals: 2 }) : "—"}
        />
        <Stat
          label={t("totalVolume")}
          value={snapshot ? formatNumber(snapshot.volume, locale, { decimals: 0 }) : "—"}
        />
        <Stat
          label={t("insideVolume")}
          value={tickRows.length > 0 ? formatNumber(insideVolume, locale, { decimals: 0 }) : "—"}
        />
        <Stat
          label={t("outsideVolume")}
          value={tickRows.length > 0 ? formatNumber(outsideVolume, locale, { decimals: 0 }) : "—"}
        />
        <Stat
          label={t("lastTradeSize")}
          value={
            lastTradeSize !== null ? formatNumber(lastTradeSize, locale, { decimals: 0 }) : "—"
          }
        />
        <Stat
          label={t("averagePrice")}
          value={avgPrice !== null ? formatNumber(avgPrice, locale, { decimals: 2 }) : "—"}
        />
      </div>
      <p className="mt-2 text-[11px] text-[var(--text-muted)]">
        {t("dayTradingAsOf", { date: limitRow.trade_date })}
      </p>
    </section>
  );
}

/** Always a solid-fill pill (never conditional on the live price actually being at that
 * limit) — 漲停 red (`--up`) / 跌停 green (`--down`), standard 紅漲綠跌. */
function LimitPricePill({
  label,
  value,
  color,
}: {
  readonly label: string;
  readonly value: string;
  readonly color: string;
}): ReactNode {
  return (
    <div>
      <p className="mb-1 text-[11px] text-[var(--text-muted)]">{label}</p>
      <p
        className="inline-block rounded-[var(--radius-pill)] px-2.5 py-1 font-num font-semibold text-[14px] text-[var(--surface)] tabular-nums"
        style={{ backgroundColor: color }}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 逐筆 list — get_price_tick (Sponsor-tier). EOD replay (~15:30 publish) — never live.
// ---------------------------------------------------------------------------

function PriceTickList({
  symbol,
  market,
  tradeDate,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly tradeDate: string | undefined;
}): ReactNode {
  const t = useTranslations("stock.realtime");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ["stock_price_tick", symbol, market, tradeDate],
    queryFn: () => getPriceTick(symbol, market, { startDate: tradeDate, endDate: tradeDate }),
    enabled: tradeDate !== undefined,
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  // `get_price_tick` returns rows in the upstream FinMind order (earliest tick first) — reverse
  // so the newest tick renders on top, matching the reference app's own 逐筆成交 ordering.
  const rows = data !== undefined && !isMaskedTeaser(data) ? [...data.rows].reverse() : [];

  if (query.isLoading || tradeDate === undefined) return <CardSkeletonBody height={280} />;
  if (!data) return null;

  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("tickTitle")}</h2>
      <p className="mb-3 text-[11px] text-[var(--warn)]">{t("tickReplayNotice")}</p>
      {locked && isMaskedTeaser(data) ? (
        <SponsorTeaser
          headline={t("tickTitle")}
          preview={data.preview}
          requiredTier={data.required_tier}
        >
          <div className="h-56 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      ) : rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("tickUnavailable")}</p>
      ) : (
        <>
          <TickRows rows={rows.slice(0, TICK_COLLAPSED_ROW_COUNT)} locale={locale} />
          {rows.length > TICK_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className={cn(
                "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              )}
            >
              <span>{t("tickExpandTo", { count: rows.length })}</span>
            </button>
          ) : null}
          <ExpandModal
            open={expanded}
            title={t("tickTitle")}
            closeLabel={t("tickCollapse")}
            onClose={() => setExpanded(false)}
          >
            <TickRows rows={rows} locale={locale} />
          </ExpandModal>
        </>
      )}
    </section>
  );
}

function TickRows({
  rows,
  locale,
}: {
  readonly rows: readonly { time: string; deal_price: number; volume: number; tick_type: string }[];
  readonly locale: string;
}): ReactNode {
  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((row, index) => {
        const color =
          row.tick_type === "1"
            ? "var(--up)"
            : row.tick_type === "2"
              ? "var(--down)"
              : "var(--text-muted)";
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: ticks share no unique field; index is this static, server-ordered list's own real position.
          <li key={index} className="flex items-center justify-between py-1.5 text-[13px]">
            <span className="font-num text-[var(--text-muted)] tabular-nums">{row.time}</span>
            <span className="font-num tabular-nums" style={{ color }}>
              {formatNumber(row.deal_price, locale, { decimals: 2 })}
            </span>
            <span className="font-num text-[var(--text)] tabular-nums">
              {formatNumber(row.volume, locale, { decimals: 0 })}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
