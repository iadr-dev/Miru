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
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { isoDaysAgo, SubTabRow, TOOLTIP_STYLE } from "@/components/stock/chip/shared";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { useInstitutionalFlow } from "@/hooks/use-stock-data";
import { Link } from "@/i18n/navigation";
import { getGovernmentBankFlows } from "@/lib/api/stock-detail";
import { fieldLabel } from "@/lib/field-labels";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

const ROLLING_NET_WINDOWS = ["5d", "10d", "20d"] as const;

type RollingNetWindow = (typeof ROLLING_NET_WINDOWS)[number];

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

/** 摘要's 法人籌碼 chart — reference app's own 摘要表 shows one bar chart of net buy/sell per
 * investor category, with a lookback-window selector (5日/10日/20日), not a flat number list.
 * `get_institutional_flow`'s `rolling_net` already pre-sums exactly those 3 windows server-side
 * (see that field's own docstring in `tools/market/`) — this just picks which window's number
 * feeds each bar, no client-side re-summing of raw rows. */
/** `_net_over_trailing`'s client-side mirror (`tools/market/`) for the one category
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

export function InstitutionalSummaryBody({
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
  const query = useInstitutionalFlow(symbol, market);
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
  // backend's own `_net_over_trailing` (tools/market/, and this file's own
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
  // `rolling_net`'s `net_5d`/`net_10d`/`net_20d` are pre-summed server-side from
  // `get_institutional_flow`'s own raw-shares `buy`/`sell` (`_net_over_trailing`,
  // tools/market/) — same TWSE T86-confirmed shares unit as `bucketedFlowByDate`'s bucket sums
  // elsewhere in this panel (see `NetFlowCell`'s own docstring); `governmentBankRollingNet`'s
  // client-side mirror sums the same raw-shares `buy`/`sell` off `get_government_bank_flows`.
  // Converted to 張 here, once, before either the chart or its data sheet renders a number.
  const points = allRollingNet
    .filter((row) => !isEmptyRow(row) && netForWindow(row) != null)
    .map((row) => ({
      investor: row.investor,
      net: (netForWindow(row) ?? 0) / 1000,
      net_5d: row.net_5d != null ? row.net_5d / 1000 : row.net_5d,
      net_10d: row.net_10d != null ? row.net_10d / 1000 : row.net_10d,
      net_20d: row.net_20d != null ? row.net_20d / 1000 : row.net_20d,
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
                // `points`/`net` is already converted to 張 above — just the inline suffix here.
                tickFormatter={(v: number) => `${formatCompactNumber(v, locale)}張`}
                width={48}
                stroke="var(--border)"
              />
              <ReferenceLine y={0} stroke="var(--border)" />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ color: "var(--text)" }}
                itemStyle={{ color: "var(--text)" }}
                cursor={{ fill: "var(--accent)", opacity: 0.08 }}
                formatter={(value) => t("lotUnit", { value: formatNumber(Number(value), locale) })}
                labelFormatter={(label) => investorLabel(tFields, String(label))}
              />
              <Bar dataKey="net" radius={3}>
                {points.map((point) => (
                  <Cell
                    key={point.investor}
                    fill={INVESTOR_CATEGORY_COLORS[point.investor] ?? "var(--chart-6)"}
                    aria-label={
                      point.net >= 0
                        ? `${investorLabel(tFields, point.investor)} ▲ ${t("lotUnit", { value: formatNumber(point.net, locale) })}`
                        : `${investorLabel(tFields, point.investor)} ▼ ${t("lotUnit", { value: formatNumber(point.net, locale) })}`
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
