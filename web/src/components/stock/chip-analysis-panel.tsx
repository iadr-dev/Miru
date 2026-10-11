"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ChipFlowsBody } from "@/components/stock/chip/broker-flows";
import { ForeignShareholdingBody } from "@/components/stock/chip/foreign-holdings";
import { InstitutionalFlowBody } from "@/components/stock/chip/institutional-flow";
import { InstitutionalSummaryBody } from "@/components/stock/chip/institutional-summary";
import { InstitutionalTotalsBody } from "@/components/stock/chip/institutional-totals";
import { MarginShortBody } from "@/components/stock/chip/margin-short";
import {
  formatSignedNumber,
  MARGIN_SUB_TABS,
  type MarginSubTab,
  SnapshotStat,
  SubTabRow,
} from "@/components/stock/chip/shared";
import {
  getSecuritiesLending,
  getStockDayTrading,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const INSTITUTIONAL_SUB_TABS = ["flow", "holdings", "summary"] as const;

type InstitutionalSubTab = (typeof INSTITUTIONAL_SUB_TABS)[number];

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
    <section className={cn("surface-card p-4 sm:p-5", SUMMARY_ROW_MIN_HEIGHT)}>
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
    <section className={cn("surface-card p-4 sm:p-5", SUMMARY_ROW_MIN_HEIGHT)}>
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("column.brokers")}</h2>
      <ChipFlowsBody symbol={symbol} market={market} />
    </section>
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
    <section className={cn("surface-card p-4 sm:p-5", INSTITUTIONAL_ROW_MIN_HEIGHT)}>
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
    <section className={cn("surface-card p-4 sm:p-5", INSTITUTIONAL_ROW_MIN_HEIGHT)}>
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
      {/* `margin_purchase_today_balance`/`short_sale_today_balance` (and the day-over-day
       * `*_change` diffed from them) are already 張-scaled — confirmed by diffing FinMind's own
       * `MarginPurchaseTodayBalance`/`ShortSaleTodayBalance` against TWSE's `MI_MARGN` feed for
       * the same stock/date: identical values, and TWSE's own column header there reads
       * "融資(交易單位)" (trading units = 張), not 股. Only the missing unit label is added, no
       * division. `day_trading.volume`/`securities_lending.volume` ARE raw 股 (confirmed against
       * TWSE's own TWTB4U 當日沖銷交易總成交股數 and the 借券 借券餘額股 feeds, both "單位:股") —
       * those two divide by 1000 before formatting. */}
      <SnapshotStat
        label={t("marginChange")}
        value={t("lotUnit", { value: formatSignedNumber(marginChange, locale) })}
        tone={marginChange >= 0 ? "up" : "down"}
      />
      <SnapshotStat
        label={t("shortChange")}
        value={t("lotUnit", { value: formatSignedNumber(shortChange, locale) })}
        tone={shortChange >= 0 ? "up" : "down"}
      />
      <SnapshotStat
        label={t("marginBalance")}
        value={t("lotUnit", {
          value: formatNumber(latestMargin.margin_purchase_today_balance, locale),
        })}
      />
      <SnapshotStat
        label={t("shortBalance")}
        value={t("lotUnit", {
          value: formatNumber(latestMargin.short_sale_today_balance, locale),
        })}
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
        value={
          latestDayTrading
            ? t("lotUnit", { value: formatNumber(latestDayTrading.volume / 1000, locale) })
            : "—"
        }
      />
      <SnapshotStat
        label={t("shortToMarginRatio")}
        value={shortToMarginRatio != null ? `${shortToMarginRatio.toFixed(2)}%` : "—"}
      />
      <SnapshotStat
        label={t("lendingSellVolume")}
        value={
          latestLending
            ? t("lotUnit", { value: formatNumber(latestLending.volume / 1000, locale) })
            : "—"
        }
      />
    </div>
  );
}
