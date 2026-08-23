"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";
import { DivergingBarChart } from "@/app/[locale]/(app)/chat/cards/diverging-bar-chart";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { StatCard } from "@/components/market/stat-card";
import { useDataUpdates } from "@/hooks/use-data-updates";
import {
  getBusinessIndicator,
  getDayTrading,
  getEtfMoneyFlow,
  getIndustryFlow,
  getInstitutionalRanking,
  getMacroIndicators,
  getMarginMaintenance,
  MarketApiError,
} from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";
const DEFAULT_SYMBOL = "2330";

/** A bounded [start, end] window ending today — `etf_money_flow` requires an explicit range;
 * the other cards' `start_date`/`end_date` stay optional (omitted → backend default window),
 * this constant only feeds the one endpoint that needs it. */
function defaultEtfWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 30);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

export function MarketPanel(): ReactNode {
  useDataUpdates();
  const t = useTranslations("market.cards");
  const tCaveat = useTranslations("chat.caveat");
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const etfWindow = defaultEtfWindow();

  const institutionalQuery = useQuery({
    queryKey: ["institutional_flow_total_market", DEFAULT_MARKET],
    queryFn: () => getInstitutionalRanking(DEFAULT_MARKET),
  });

  const marginQuery = useQuery({
    queryKey: ["margin_maintenance_ratio", DEFAULT_MARKET],
    queryFn: () => getMarginMaintenance(DEFAULT_MARKET),
  });

  const dayTradingQuery = useQuery({
    queryKey: ["day_trading", DEFAULT_MARKET, symbol],
    queryFn: () => getDayTrading(symbol, DEFAULT_MARKET),
    enabled: symbol.trim().length > 0,
  });

  const etfMoneyFlowQuery = useQuery({
    queryKey: ["active_etf_holding_change", DEFAULT_MARKET, etfWindow.startDate, etfWindow.endDate],
    queryFn: () => getEtfMoneyFlow(DEFAULT_MARKET, etfWindow.startDate, etfWindow.endDate),
  });

  const industryFlowQuery = useQuery({
    queryKey: ["industry_chain_money_flow", DEFAULT_MARKET],
    queryFn: () => getIndustryFlow(DEFAULT_MARKET),
  });

  const macroQuery = useQuery({
    queryKey: ["macro", DEFAULT_MARKET],
    queryFn: () => getMacroIndicators(),
  });

  const businessIndicatorQuery = useQuery({
    queryKey: ["business_indicator", DEFAULT_MARKET],
    queryFn: () => getBusinessIndicator(DEFAULT_MARKET),
  });

  return (
    <div className="grid grid-cols-1 gap-5 p-6 md:grid-cols-2 xl:grid-cols-3">
      <MarketCard title={t("institutionalRanking.title")}>
        <QueryState query={institutionalQuery}>
          {(data) => {
            // The endpoint returns a multi-day history (one row per category per trade_date);
            // `DivergingBarChart` renders one snapshot, so only the latest day's rows go in —
            // otherwise every category renders once per day, with colliding React keys.
            const latestDate = data.rows.at(-1)?.trade_date;
            const latestRows = data.rows.filter((row) => row.trade_date === latestDate);
            return (
              <>
                <DivergingBarChart data={{ rows: latestRows }} />
                <Caveat text={tCaveat("nonTrading")} />
              </>
            );
          }}
        </QueryState>
      </MarketCard>

      <MarketCard title={t("marginMaintenance.title")}>
        <QueryState query={marginQuery}>
          {(data) => {
            if (isMaskedTeaser(data)) {
              return (
                <SponsorTeaser
                  headline={t("marginMaintenance.title")}
                  preview={data.preview}
                  requiredTier={data.required_tier}
                >
                  <MarginMaintenancePreview />
                </SponsorTeaser>
              );
            }
            const rows = data.rows;
            const latest = rows.at(-1);
            const prior = rows.length > 1 ? rows.at(-2) : undefined;
            return (
              <>
                <StatCard
                  label={t("marginMaintenance.latest")}
                  value={latest?.total_exchange_margin_maintenance ?? null}
                  priorValue={prior?.total_exchange_margin_maintenance ?? null}
                  unit="%"
                  decimals={2}
                />
                <ChartWithDataSheet rows={rows}>
                  <MarginMaintenancePreview />
                </ChartWithDataSheet>
                <Caveat text={tCaveat("nonTrading")} />
              </>
            );
          }}
        </QueryState>
      </MarketCard>

      <MarketCard title={t("dayTrading.title")}>
        <div className="mb-3">
          <label
            htmlFor="market-day-trading-symbol"
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
          >
            {t("dayTrading.symbolLabel")}
          </label>
          <input
            id="market-day-trading-symbol"
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.trim())}
            placeholder={t("dayTrading.symbolPlaceholder")}
            aria-label={t("dayTrading.symbolLabel")}
            className={cn(
              "min-h-11 w-full max-w-[200px] rounded-[var(--radius-input)] border border-[var(--border)]",
              "bg-[var(--surface)] px-3 text-[14px] text-[var(--text)] placeholder:text-[var(--text-muted)]",
              "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            )}
          />
        </div>
        <QueryState query={dayTradingQuery}>
          {(data) => (
            <>
              <ChartWithDataSheet rows={data.rows}>
                <p className="text-[13px] text-[var(--text-muted)]">
                  {data.rows.length > 0 ? `${data.rows.length} 筆` : t("empty")}
                </p>
              </ChartWithDataSheet>
              <Caveat text={tCaveat("nonTrading")} />
            </>
          )}
        </QueryState>
      </MarketCard>

      <MarketCard title={t("etfMoneyFlow.title")}>
        <QueryState query={etfMoneyFlowQuery}>
          {(data) => {
            if (isMaskedTeaser(data)) {
              return (
                <SponsorTeaser
                  headline={t("etfMoneyFlow.title")}
                  preview={data.preview}
                  requiredTier={data.required_tier}
                >
                  <EtfMoneyFlowPreview />
                </SponsorTeaser>
              );
            }
            if (!data.data_available) {
              return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
            }
            return (
              <>
                <RankingList label={t("etfMoneyFlow.inflow")} rows={data.inflow_ranking} />
                <RankingList label={t("etfMoneyFlow.outflow")} rows={data.outflow_ranking} />
                <Caveat text={tCaveat("nonTrading")} />
              </>
            );
          }}
        </QueryState>
      </MarketCard>

      <MarketCard title={t("industryFlow.title")}>
        <QueryState query={industryFlowQuery}>
          {(data) => {
            if (isMaskedTeaser(data)) {
              return (
                <SponsorTeaser
                  headline={t("industryFlow.title")}
                  preview={data.preview}
                  requiredTier={data.required_tier}
                >
                  <IndustryFlowPreview />
                </SponsorTeaser>
              );
            }
            return (
              <>
                <ChartWithDataSheet rows={data.rows}>
                  <IndustryFlowPreview />
                </ChartWithDataSheet>
                <Caveat text={tCaveat("nonTrading")} />
              </>
            );
          }}
        </QueryState>
      </MarketCard>

      <MarketCard title={t("macro.title")}>
        <QueryState query={macroQuery}>
          {(data) => {
            if (isMaskedTeaser(data)) {
              return (
                <SponsorTeaser
                  headline={t("macro.title")}
                  preview={data.preview}
                  requiredTier={data.required_tier}
                >
                  <MacroPreview />
                </SponsorTeaser>
              );
            }
            return (
              <>
                <MacroStatRows data={data} />
                <BusinessIndicatorRow query={businessIndicatorQuery} />
                <Caveat text={tCaveat("nonTrading")} />
              </>
            );
          }}
        </QueryState>
      </MarketCard>
    </div>
  );
}

function MarketCard({ title, children }: { title: string; children: ReactNode }): ReactNode {
  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{title}</h2>
      {children}
    </section>
  );
}

/** Shared loading/empty/error branching for every card's query (web/CLAUDE.md §6) — a shaped
 * skeleton while loading, a calm inline error with retry on failure, and the caller's own render
 * function once data is present (which itself may still show a masked teaser or an empty state). */
function QueryState<T>({
  query,
  children,
}: {
  query: {
    isLoading: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => void;
  };
  children: (data: T) => ReactNode;
}): ReactNode {
  const t = useTranslations("market.cards");

  if (query.isLoading) {
    return (
      <div className="animate-pulse space-y-2" aria-hidden>
        <div className="h-24 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        <div className="h-4 w-2/3 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </div>
    );
  }
  if (query.isError) {
    const message = query.error instanceof MarketApiError ? query.error.message : t("error");
    return (
      <div
        role="alert"
        className="rounded-[var(--radius-input)] border border-[var(--warn)]/40 bg-[var(--surface-2)] p-3"
      >
        <p className="text-[13px] text-[var(--text)]">{message}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-2 font-medium text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {t("retry")}
        </button>
      </div>
    );
  }
  if (query.data === undefined) {
    return null;
  }
  return <>{children(query.data)}</>;
}

function MarginMaintenancePreview(): ReactNode {
  return <div className="h-16 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />;
}
function EtfMoneyFlowPreview(): ReactNode {
  return <div className="h-24 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />;
}
function IndustryFlowPreview(): ReactNode {
  return <div className="h-24 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />;
}
function MacroPreview(): ReactNode {
  return <div className="h-24 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />;
}

function RankingList({
  label,
  rows,
}: {
  label: string;
  rows: { component_stock_id: string; component_stock_name: string; net_shares_flow: number }[];
}): ReactNode {
  const locale = useLocale();
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="mb-3">
      <p className="mb-1 text-[13px] text-[var(--text-muted)]">{label}</p>
      <ul className="space-y-1">
        {rows.slice(0, 5).map((row) => (
          <li
            key={row.component_stock_id}
            className="flex items-center justify-between text-[13px] text-[var(--text)]"
          >
            <span>
              {row.component_stock_name} ({row.component_stock_id})
            </span>
            <span className="font-num tabular-nums">
              {formatNumber(row.net_shares_flow, locale)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Every macro sub-series carries many trade-date rows per `symbol` — keeps only the latest row
 * per symbol (input order is chronological, so the last occurrence wins) both to show current
 * values and to give each stat tile a unique React key. */
function latestBySymbol<Row extends { symbol: string }>(rows: Row[]): Row[] {
  const bySymbol = new Map<string, Row>();
  for (const row of rows) {
    bySymbol.set(row.symbol, row);
  }
  return [...bySymbol.values()];
}

function MacroStatRows({
  data,
}: {
  data: {
    exchange_rate: { symbol: string; cash_buy: number }[];
    gold_price: { price: number }[];
    crude_oil_prices: { symbol: string; price: number }[];
    government_bond_yield: { symbol: string; value: number }[];
    fear_greed_index: { fear_greed: number; fear_greed_emotion: string }[];
  };
}): ReactNode {
  const t = useTranslations("market.cards.macro");
  const locale = useLocale();
  const latestGold = data.gold_price.at(-1);
  const latestFearGreed = data.fear_greed_index.at(-1);
  const exchangeRates = latestBySymbol(data.exchange_rate);
  const crudeOilPrices = latestBySymbol(data.crude_oil_prices);
  const bondYields = latestBySymbol(data.government_bond_yield);

  return (
    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
      {exchangeRates.slice(0, 3).map((row) => (
        <div key={row.symbol} className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
          <p className="text-[11px] text-[var(--text-muted)]">
            {t("exchangeRate")} {row.symbol}
          </p>
          <p className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(row.cash_buy, locale, { decimals: 2 })}
          </p>
        </div>
      ))}
      {latestGold ? (
        <div className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
          <p className="text-[11px] text-[var(--text-muted)]">{t("goldPrice")}</p>
          <p className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(latestGold.price, locale, { decimals: 2 })}
          </p>
        </div>
      ) : null}
      {crudeOilPrices.slice(0, 2).map((row) => (
        <div key={row.symbol} className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
          <p className="text-[11px] text-[var(--text-muted)]">
            {t("crudeOilPrices")} {row.symbol}
          </p>
          <p className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(row.price, locale, { decimals: 2 })}
          </p>
        </div>
      ))}
      {bondYields.slice(0, 1).map((row) => (
        <div key={row.symbol} className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
          <p className="text-[11px] text-[var(--text-muted)]">{t("governmentBondYield")}</p>
          <p className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(row.value, locale, { decimals: 2 })}%
          </p>
        </div>
      ))}
      {latestFearGreed ? (
        <div className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
          <p className="text-[11px] text-[var(--text-muted)]">{t("fearGreedIndex")}</p>
          <p className="font-num text-[15px] text-[var(--text)] tabular-nums">
            {formatNumber(latestFearGreed.fear_greed, locale, { decimals: 0 })}
            {" · "}
            {latestFearGreed.fear_greed_emotion}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function BusinessIndicatorRow({
  query,
}: {
  query: {
    isLoading: boolean;
    isError: boolean;
    data:
      | { locked: true; preview: string; required_tier: "premium" | "pro" }
      | { rows: { date: string; monitoring: number; monitoring_color: string }[] }
      | undefined;
  };
}): ReactNode {
  const t = useTranslations("market.cards.macro");
  if (query.isLoading || query.isError || query.data === undefined || "locked" in query.data) {
    return null;
  }
  const latest = query.data.rows.at(-1);
  if (!latest) {
    return null;
  }
  return (
    <div className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2 text-[13px] text-[var(--text)]">
      {t("businessIndicator")}: {latest.monitoring} ({latest.monitoring_color})
    </div>
  );
}
