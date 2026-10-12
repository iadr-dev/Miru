"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { GaugeBand } from "@/components/market/gauge-card";
import { GaugeCard } from "@/components/market/gauge-card";
import { ExpandModal } from "@/components/ui/expand-modal";
import { TableWithActions } from "@/components/ui/table-actions";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { directionColor, directionGlyph } from "@/lib/signed";
import type { MarginMaintenanceRow, MarginShortTotalMarketRow } from "@/types/market";

/** FinMind's `TaiwanStockTotalMarginPurchaseShortSale`'s real `name` category values —
 * confirmed live against `/market/margin-short-balance`: `MarginPurchase`/`ShortSale` (share
 * counts) and `MarginPurchaseMoney` (NT$ amount; there is no corresponding `ShortSaleMoney` row
 * in this dataset). Maps to an i18n key (`market.cards.marginMaintenance.category*`) so the
 * English locale shows a real translation, not leftover 中文 — an unrecognized value (a future
 * FinMind addition) falls back to the raw string rather than a guessed translation. */
const MARGIN_SHORT_CATEGORY_LABEL_KEYS: Record<string, string> = {
  MarginPurchase: "categoryMarginPurchase",
  MarginPurchaseMoney: "categoryMarginPurchaseMoney",
  ShortSale: "categoryShortSale",
};

/** TWSE's own 融資維持率 warn/safe reference bands (150%/整戶追繳線 conventions) — a real,
 * published threshold set, not an invented cutoff; used only to color the real fetched value.
 * `labelKey` resolves through `t()` at render time (`market.cards.marginMaintenance.band*`) —
 * never a hardcoded zh string, so the English locale renders real English, not leftover 中文. */
// TW convention (matches the mockup's own gauge SVG): 警戒(danger) = --up (red),
// 安全(safe) = --down (green) — the 紅漲綠跌 pairing extended to a risk gauge, not a price.
type MarginBand = { min: number; max: number; labelKey: string; color: string };
const MARGIN_MAINTENANCE_BANDS: readonly MarginBand[] = [
  { min: 0, max: 130, labelKey: "bandDanger", color: "var(--up)" },
  { min: 130, max: 166, labelKey: "bandNeutral", color: "var(--warn)" },
  { min: 166, max: Number.POSITIVE_INFINITY, labelKey: "bandSafe", color: "var(--down)" },
];

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const HISTORY_PREVIEW_ROWS = 8;

type TrendAnchor = { key: string; labelKey: string; daysAgo: number };

/** The reference's 今日/前一交易日/一週前/一個月前/一年前 rows — each is a REAL row from the
 * fetched series, matched to the closest available trade_date on/before the target offset, never
 * an interpolated or fabricated value. A row is omitted (not zero-filled) when the fetched range
 * doesn't reach that far back — see `nearestOnOrBefore`. */
const TREND_ANCHORS: readonly TrendAnchor[] = [
  { key: "today", labelKey: "today", daysAgo: 0 },
  { key: "priorDay", labelKey: "priorDay", daysAgo: 1 },
  { key: "weekAgo", labelKey: "weekAgo", daysAgo: 7 },
  { key: "monthAgo", labelKey: "monthAgo", daysAgo: 30 },
  { key: "yearAgo", labelKey: "yearAgo", daysAgo: 365 },
];

function nearestOnOrBefore(
  rowsByDate: Map<string, MarginMaintenanceRow>,
  sortedDates: readonly string[],
  targetDate: Date,
): MarginMaintenanceRow | null {
  const targetIso = targetDate.toISOString().slice(0, 10);
  // `sortedDates` is ascending — walk backward for the latest date <= target.
  for (let i = sortedDates.length - 1; i >= 0; i--) {
    const date = sortedDates[i];
    if (date !== undefined && date <= targetIso) {
      return rowsByDate.get(date) ?? null;
    }
  }
  return null;
}

function bandFor(bands: readonly GaugeBand[], value: number): GaugeBand | undefined {
  return bands.find((b) => value >= b.min && value < b.max);
}

/** Shared render for one delta cell (▲/▼ + signed %) — used by both the inline preview table and
 * the expand-modal table so a shared date never shows two independently-formatted deltas. */
function renderMarginDelta(against: number | null, value: number, locale: string): ReactNode {
  if (against === null) {
    return <span className="text-[var(--text-muted)]">—</span>;
  }
  const delta = value - against;
  return (
    <span className="font-num tabular-nums" style={{ color: directionColor(delta) }}>
      {directionGlyph(delta)}
      {formatNumber(Math.abs(delta), locale, { decimals: 2 })}%
    </span>
  );
}

/**
 * 融資維持率 — the real whole-market margin-maintenance-ratio gauge, its own reference-band
 * trend rows (today/prior-day/week/month/year, each a genuine fetched row — never fabricated),
 * and an expandable full history chart+table. Fills the space the 恐懼與貪婪 gauge (US-only, no
 * TW source) vacated in this card's original mockup position.
 */
export function MarginMaintenanceCard({
  rows,
  shortBalanceRows,
}: {
  rows: readonly MarginMaintenanceRow[];
  shortBalanceRows: readonly MarginShortTotalMarketRow[];
}): ReactNode {
  const t = useTranslations("market.cards.marginMaintenance");
  const locale = useLocale();
  const [historyOpen, setHistoryOpen] = useState(false);

  const bands: readonly GaugeBand[] = MARGIN_MAINTENANCE_BANDS.map((b) => ({
    min: b.min,
    max: b.max,
    color: b.color,
    label: t(b.labelKey),
  }));

  const sorted = [...rows].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const latest = sorted.at(-1);
  const rowsByDate = new Map(sorted.map((row) => [row.trade_date, row]));
  const sortedDates = sorted.map((row) => row.trade_date);

  const latestShortBalanceDate = shortBalanceRows.reduce<string | null>(
    (max, row) => (max === null || row.trade_date > max ? row.trade_date : max),
    null,
  );
  const latestShortBalanceRows = shortBalanceRows.filter(
    (row) => row.trade_date === latestShortBalanceDate,
  );

  const trendRows = latest
    ? TREND_ANCHORS.map((anchor) => {
        const target = new Date(`${latest.trade_date}T00:00:00Z`);
        target.setTime(target.getTime() - anchor.daysAgo * MS_PER_DAY);
        const row = nearestOnOrBefore(rowsByDate, sortedDates, target);
        return { anchor, row };
      }).filter((entry) => entry.row !== null)
    : [];

  // Shared per-row deltas (較前一交易日/週/月/年) — computed once over the FULL sorted series so
  // the inline preview and the expand-modal table render the same numbers for a shared date,
  // never two independently-truncated calculations that could silently drift apart.
  const rowsWithDeltas = sorted.map((row, index) => {
    const target = new Date(`${row.trade_date}T00:00:00Z`);
    const deltaAgainst = (daysAgo: number): number | null => {
      if (daysAgo === 0) {
        return index > 0 ? (sorted[index - 1]?.total_exchange_margin_maintenance ?? null) : null;
      }
      const anchor = new Date(target);
      anchor.setTime(anchor.getTime() - daysAgo * MS_PER_DAY);
      const priorRow = nearestOnOrBefore(rowsByDate, sortedDates.slice(0, index), anchor);
      return priorRow?.total_exchange_margin_maintenance ?? null;
    };
    return {
      row,
      priorDayValue: deltaAgainst(0),
      weekAgoValue: deltaAgainst(7),
      monthAgoValue: deltaAgainst(30),
      yearAgoValue: deltaAgainst(365),
    };
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      <div className="mt-3 flex flex-wrap items-center gap-6">
        <GaugeCard
          title={t("title")}
          value={latest?.total_exchange_margin_maintenance ?? null}
          unit="%"
          domainMin={80}
          domainMax={220}
          bands={bands}
          leftAxisLabel={bands[0]?.label ?? ""}
          rightAxisLabel={bands.at(-1)?.label ?? ""}
        />

        {trendRows.length > 0 ? (
          <ul className="min-w-[220px] flex-1 space-y-1.5">
            {trendRows.map(({ anchor, row }) => {
              if (!row) {
                return null;
              }
              const value = row.total_exchange_margin_maintenance;
              const band = bandFor(bands, value);
              return (
                <li
                  key={anchor.key}
                  className="flex items-center justify-between gap-2 text-[12.5px]"
                >
                  <span className="shrink-0 text-[var(--text-muted)]">{t(anchor.labelKey)}</span>
                  <span
                    aria-hidden
                    className="flex-1 border-[var(--border)] border-b border-dotted"
                  />
                  <span
                    className="shrink-0 font-num tabular-nums"
                    style={{ color: band?.color ?? "var(--text)" }}
                  >
                    {formatNumber(value, locale, { decimals: 1 })}% {band?.label ?? ""}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>

      {sorted.length > 1 ? (
        <div className="mt-4" style={{ width: "100%", height: 140 }}>
          <ResponsiveContainer>
            <LineChart data={sorted}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis
                dataKey="trade_date"
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                stroke="var(--border)"
                minTickGap={40}
              />
              <YAxis
                domain={["auto", "auto"]}
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                tickFormatter={(v: number) => `${v}%`}
                width={44}
                stroke="var(--border)"
              />
              <Tooltip
                contentStyle={{
                  background: "var(--surface-2)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-input)",
                  color: "var(--text)",
                }}
                labelStyle={{ color: "var(--text)" }}
                itemStyle={{ color: "var(--text)" }}
                cursor={{ fill: "var(--accent)", opacity: 0.08 }}
                formatter={(value) => [
                  `${formatNumber(value as number, locale, { decimals: 2 })}%`,
                  t("title"),
                ]}
              />
              <Line
                type="monotone"
                dataKey="total_exchange_margin_maintenance"
                stroke="var(--accent)"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}

      {latestShortBalanceRows.length > 0 ? (
        <div className="mt-3">
          <div className="grid grid-cols-[1.4fr_1fr_1fr_1fr] items-center gap-2 border-[var(--border)] border-b pb-1 text-[11px] text-[var(--text-muted)]">
            <span>{t("shortBalanceCategory")}</span>
            <span className="text-right">{t("shortBalanceBuySell")}</span>
            <span className="text-right">{t("shortBalanceTodayBalance")}</span>
            <span className="text-right">{t("shortBalanceReturn")}</span>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {latestShortBalanceRows.map((row) => (
              <li
                key={row.name}
                className="grid grid-cols-[1.4fr_1fr_1fr_1fr] items-center gap-2 py-1 text-[12.5px]"
              >
                <span className="text-[var(--text-muted)]">
                  {MARGIN_SHORT_CATEGORY_LABEL_KEYS[row.name]
                    ? t(MARGIN_SHORT_CATEGORY_LABEL_KEYS[row.name] as string)
                    : row.name}
                </span>
                <span className="text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.buy, locale)} / {formatNumber(row.sell, locale)}
                </span>
                <span className="text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.today_balance, locale)}
                </span>
                <span
                  className="text-right font-num tabular-nums"
                  style={{ color: directionColor(row.return_) }}
                >
                  {directionGlyph(row.return_)}
                  {formatNumber(Math.abs(row.return_), locale)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sorted.length > 1 ? (
        <div className="mt-4 overflow-x-auto">
          <div className="grid grid-cols-[80px_1fr_1fr_1fr_1fr_110px] items-center gap-3 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]">
            <span>{t("historyDate")}</span>
            <span className="text-right">{t("historyChange")}</span>
            <span className="text-right">{t("historyChangeWeek")}</span>
            <span className="text-right">{t("historyChangeMonth")}</span>
            <span className="text-right">{t("historyChangeYear")}</span>
            <span className="text-right">{t("title")}</span>
          </div>
          <ul className="max-h-[230px] divide-y divide-[var(--border)] overflow-y-auto">
            {rowsWithDeltas
              .slice()
              .reverse()
              .slice(0, HISTORY_PREVIEW_ROWS)
              .map(({ row, priorDayValue, weekAgoValue, monthAgoValue, yearAgoValue }) => {
                const value = row.total_exchange_margin_maintenance;
                const band = bandFor(bands, value);
                const label = band?.label ?? "";
                const color = band?.color ?? "var(--text)";
                const renderDelta = (against: number | null): ReactNode =>
                  renderMarginDelta(against, value, locale);
                return (
                  <li
                    key={row.trade_date}
                    className="grid grid-cols-[80px_1fr_1fr_1fr_1fr_110px] items-center gap-3 py-1.5 text-[12.5px]"
                  >
                    <span className="text-[var(--text-muted)]">{row.trade_date}</span>
                    <span className="text-right">{renderDelta(priorDayValue)}</span>
                    <span className="text-right">{renderDelta(weekAgoValue)}</span>
                    <span className="text-right">{renderDelta(monthAgoValue)}</span>
                    <span className="text-right">{renderDelta(yearAgoValue)}</span>
                    <span className="text-right font-num tabular-nums" style={{ color }}>
                      {formatNumber(value, locale, { decimals: 2 })}% {label}
                    </span>
                  </li>
                );
              })}
          </ul>
        </div>
      ) : null}

      {/* `mt-auto` pins this button to the card's bottom edge — see `LeaderboardCard`'s matching
       * comment on its own footer group for why (equal-height grid cell in `top-split.tsx`). */}
      <div className="mt-auto pt-3">
        {sorted.length > 1 ? (
          <button
            type="button"
            onClick={() => setHistoryOpen(true)}
            className={cn(
              "flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
              "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
            )}
          >
            <span>{t("expandHistory")}</span>
          </button>
        ) : null}
      </div>

      <ExpandModal
        open={historyOpen}
        title={t("title")}
        closeLabel={t("collapseHistory")}
        onClose={() => setHistoryOpen(false)}
      >
        <TableWithActions className="text-[12.5px]">
          <thead>
            <tr className="bg-[var(--surface-2)]">
              <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
                {t("historyDate")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("historyChange")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("historyChangeWeek")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("historyChangeMonth")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("historyChangeYear")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("title")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rowsWithDeltas
              .slice()
              .reverse()
              .slice(0, 100)
              .map(({ row, priorDayValue, weekAgoValue, monthAgoValue, yearAgoValue }) => {
                const value = row.total_exchange_margin_maintenance;
                return (
                  <tr key={row.trade_date}>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                      {row.trade_date}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right">
                      {renderMarginDelta(priorDayValue, value, locale)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right">
                      {renderMarginDelta(weekAgoValue, value, locale)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right">
                      {renderMarginDelta(monthAgoValue, value, locale)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right">
                      {renderMarginDelta(yearAgoValue, value, locale)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                      {formatNumber(value, locale, { decimals: 2 })}%
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </TableWithActions>
      </ExpandModal>
    </section>
  );
}
