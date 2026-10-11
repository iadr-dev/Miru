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
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS,
  type MarginSubTab,
  NetFlowCell,
  TOOLTIP_STYLE,
} from "@/components/stock/chip/shared";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getPriceHistory } from "@/lib/api/market";
import {
  getSecuritiesLending,
  getStockDayTrading,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import type { Market } from "@/types/settings";
import type { DayTradingRow, MarginShortRow } from "@/types/stock-detail";

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
              labelStyle={{ color: "var(--text)" }}
              itemStyle={{ color: "var(--text)" }}
              cursor={{ fill: "var(--accent)", opacity: 0.08 }}
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

export function MarginShortBody({
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
      // `balance`/`cashRepayment` are `margin_purchase_*`/`short_sale_*` — already 張-scaled
      // (see `SnapshotStat`'s own comment above for the TWSE MI_MARGN cross-check); inline
      // suffix only, no division.
      barTickFormatter={(v) => `${formatCompactNumber(v, locale)}張`}
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
            {t("lotUnit", { value: formatNumber(row.balance, locale) })}
          </td>
          <NetFlowCell value={row.change} locale={locale} />
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {row.usage != null ? `${row.usage.toFixed(2)}%` : "—"}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {t("lotUnit", { value: formatNumber(row.cashRepayment, locale) })}
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
  // `volume` is raw SHARES — confirmed against TWSE's own TWTB4U 當日沖銷交易統計資訊 feed
  // (當日沖銷交易總成交股數, "股" in the field name itself). `buy_amount`/`sell_amount` are TWD
  // money amounts, not a share count — left as-is (no 張 conversion applies to a currency value).
  const points = rows.map((row) => ({ ...row, volume: row.volume / 1000 }));
  return (
    <MarginShortChartShell
      rows={points}
      barKey="volume"
      barColor="var(--chart-6)"
      barName={t("dayTradingVolume")}
      barTickFormatter={(v) => `${formatCompactNumber(v, locale)}張`}
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
            {t("lotUnit", { value: formatNumber(row.volume, locale) })}
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
            {t("lotUnit", { value: formatNumber(row.margin_balance, locale) })}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {t("lotUnit", { value: formatNumber(row.short_balance, locale) })}
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
  // `volume` (and the `balance` running sum of it) is raw SHARES — confirmed against TWSE's own
  // 借券系統與證商/證金營業處所借券餘額合計表 feed, whose own field names carry "股"
  // (前日借券餘額(1)股 etc.) — divided to 張 here, once, before either the chart or table renders
  // a number.
  let cumulative = 0;
  const points = datesAsc.map((trade_date) => {
    const entry = byDate.get(trade_date);
    const volume = (entry?.volume ?? 0) / 1000;
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
      barTickFormatter={(v) => `${formatCompactNumber(v, locale)}張`}
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
            {t("lotUnit", { value: formatNumber(row.balance, locale) })}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {t("lotUnit", { value: formatNumber(row.volume, locale) })}
          </td>
          <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
            {row.fee_rate.toFixed(2)}%
          </td>
        </tr>
      )}
    />
  );
}
