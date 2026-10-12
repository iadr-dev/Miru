"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import {
  formatSignedNumber,
  isoDaysAgo,
  SnapshotStat,
  SubTabRow,
} from "@/components/stock/chip/shared";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getForeignShareholding, getStockChipFlows } from "@/lib/api/stock-detail";
import { formatNumber } from "@/lib/format-number";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";
import type { ChipBrokerTopRow } from "@/types/stock-detail";

/** 分點's 買賣超 tab period selector — days back from today, mapped to `getStockChipFlows`'s
 * `start_date` param. `get_chip_flows`'s upstream (`TaiwanStockTradingDailyReport`) is
 * single-trading-day-per-call, so the tool fans out to one call per day and merges — bounded
 * server-side to `_CHIP_BROKER_MAX_TRADING_DAYS` (10, see `tools/market/`) so one page load
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

/** 分點's 買賣超 tab — period selector (近日/5日/10日, bounded to the backend's per-trading-day
 * fan-out cap — see `BROKER_LOOKBACK_WINDOWS`'s own docstring), a 主力籌碼 stat block, and a
 * 買超Top15/賣超Top15 toggle (one table visible at a time, matching the reference app's own
 * red-highlighted-pill pattern — not both stacked) with 買賣超/買張/賣張/成交比重 columns.
 * `total_volume`/`main_force_net`/`concentration_days_pct`/`buy_top`/`sell_top` are all computed
 * server-side now (`GetChipFlowsTool.run`, not this component) — governance.md §4's row-level
 * preview exception (`supports_row_preview=True`) needs the ranking to happen backend-side so
 * the mask applies to the SAME rows this table renders, not to the raw per-day feed underneath
 * them; see `tools/market/`'s own `_broker_top_rows`/`_mask_broker_top_rows_beyond`. 成交比重
 * is each broker's share of the window's TOTAL REPORTED 分點 volume (`total_volume`), not
 * exchange-wide turnover — `get_chip_flows` only carries branch-level rows, no whole-market
 * volume figure, so this is the honest denominator available. 主力集中% divides `main_force_net`
 * by that same `total_volume`; 佔股本比重 divides `main_force_net` by
 * `get_foreign_shareholding`'s `number_of_shares_issued` (a SEPARATE public capability, still
 * fetched client-side — not masked, no reason to move it server-side). */
export function ChipFlowsBody({
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
  const data = query.data;
  if (data.buy_top.length === 0 && data.sell_top.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const sharesIssued = [...(shareholdingQuery.data?.rows ?? [])]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .at(-1)?.number_of_shares_issued;
  const sharesOfCapital =
    sharesIssued && sharesIssued > 0 ? (data.main_force_net / sharesIssued) * 100 : null;
  const turnoverRatio =
    sharesIssued && sharesIssued > 0 ? (data.total_volume / sharesIssued) * 100 : null;

  return (
    <div>
      <SubTabRow
        tabs={BROKER_LOOKBACK_WINDOWS}
        active={window}
        onChange={setWindow}
        labelFor={(w) => t(`brokerLookbackWindow.${w}`)}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 font-num text-[13px] tabular-nums">
        {/* `main_force_net`/`total_volume` are both sums of `get_chip_flows`'s (`get_chip_broker`
         * → FinMind `TaiwanStockTradingDailyReport`) own `buy`/`sell` — that dataset's own doc
         * note states plainly "buy/sell share counts are still correct" (raw 股), same as every
         * other 分點/三大法人 shares field in this panel — divided to 張 before formatting.
         * `sharesOfCapital`/`mainForceConcentration`/`turnoverRatio`/`concentrationDays` are all
         * %, unaffected by the unit change (both sides of each ratio are shares, so the ratio
         * itself doesn't change when both are scaled by the same 1000). */}
        <SnapshotStat
          label={t("mainForceNet")}
          value={t("lotUnit", { value: formatSignedNumber(data.main_force_net / 1000, locale) })}
          tone={data.main_force_net >= 0 ? "up" : "down"}
        />
        <SnapshotStat
          label={t("mainForceConcentration")}
          value={
            data.total_volume > 0
              ? `${((data.main_force_net / data.total_volume) * 100).toFixed(2)}%`
              : "—"
          }
        />
        <SnapshotStat
          label={t("sharesOfCapital")}
          value={sharesOfCapital != null ? `${sharesOfCapital.toFixed(2)}%` : "—"}
        />
        <SnapshotStat
          label={t("concentrationDays")}
          value={
            data.concentration_days_pct != null ? `${data.concentration_days_pct.toFixed(0)}%` : "—"
          }
        />
        <SnapshotStat
          label={t("brokerTotalVolume")}
          value={t("lotUnit", { value: formatNumber(data.total_volume / 1000, locale) })}
        />
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
        {data.latest_trade_date ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("asOfDate", { date: data.latest_trade_date })}
          </span>
        ) : null}
      </div>
      <BrokerTopTable
        title={t(direction === "buy" ? "buyTop15" : "sellTop15")}
        rows={direction === "buy" ? data.buy_top : data.sell_top}
        totalVolume={data.total_volume}
        locale={locale}
      />
    </div>
  );
}

const BROKER_TOP_COLLAPSED_ROW_COUNT = 8;

/** governance.md §4 row-level preview exception's locked-row placeholder — the row's own value
 * is never sent for a `locked` row (nulled server-side in `tools/market/`), so this renders a
 * fixed placeholder + lock icon rather than reading any field, mirroring
 * `tracker-panel.tsx`'s own `LockedValueCell`. */
function LockedBrokerCell(): ReactNode {
  return (
    <span className="inline-flex items-center justify-end gap-1 text-[var(--text-muted)]">
      <LockIcon size={11} weight="regular" aria-hidden />
      <span aria-hidden className="tabular-nums">
        ••••
      </span>
    </span>
  );
}

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
  readonly rows: readonly ChipBrokerTopRow[];
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
  readonly rows: readonly ChipBrokerTopRow[];
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
            if (row.locked || row.net === null || row.buy === null || row.sell === null) {
              return (
                <tr key={`locked-${row.rank}`} className="border-[var(--border)] border-b">
                  <td className="py-1.5 text-[var(--text-muted)]">
                    <span className="inline-flex items-center gap-1.5">
                      <LockIcon size={11} weight="regular" aria-hidden />
                      <span aria-hidden>••••</span>
                      <span className="font-num text-[11px] tabular-nums">#{row.rank}</span>
                    </span>
                  </td>
                  <td className="py-1.5 text-right font-num tabular-nums">
                    <LockedBrokerCell />
                  </td>
                  <td className="py-1.5 text-right font-num tabular-nums">
                    <LockedBrokerCell />
                  </td>
                  <td className="py-1.5 text-right font-num tabular-nums">
                    <LockedBrokerCell />
                  </td>
                  <td className="py-1.5 text-right font-num tabular-nums">
                    <LockedBrokerCell />
                  </td>
                </tr>
              );
            }
            const share = totalVolume > 0 ? ((row.buy + row.sell) / totalVolume) * 100 : 0;
            return (
              <tr key={row.broker_id} className="border-[var(--border)] border-b">
                <td className="py-1.5 text-[var(--text)]">{row.broker_name}</td>
                <td
                  className="py-1.5 text-right font-num tabular-nums"
                  style={{ color: row.net >= 0 ? "var(--up)" : "var(--down)" }}
                >
                  {/* `net`/`buy`/`sell` here are `ChipBrokerTopRow`'s backend-ranked figures,
                   * computed server-side from `ChipBrokerRow`'s own raw-shares `buy`/`sell` (see
                   * `ChipFlowsBody`'s own comment above for the FinMind-doc confirmation) —
                   * divided to 張 here to match this table's own 買張/賣張 header labels. */}
                  {row.net >= 0 ? "▲ " : "▼ "}
                  {t("lotUnit", { value: formatNumber(Math.abs(row.net) / 1000, locale) })}
                </td>
                <td className="py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {t("lotUnit", { value: formatNumber(row.buy / 1000, locale) })}
                </td>
                <td className="py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {t("lotUnit", { value: formatNumber(row.sell / 1000, locale) })}
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
