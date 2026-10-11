"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";

export const MARGIN_SUB_TABS = ["margin", "short", "ratio", "dayTrading", "lending"] as const;

export type MarginSubTab = (typeof MARGIN_SUB_TABS)[number];

export function isoDaysAgo(days: number): string {
  const ms = Date.now() - days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

// The reference app's own 三大法人 grouping — `get_institutional_flow`'s 5 real FinMind
// categories collapsed into the 3 buckets every TW retail chip-analysis view uses: 外資
// (Foreign_Investor + Foreign_Dealer_Self, both foreign-desk activity), 投信 (Investment_Trust
// alone), 自營商 (Dealer_self + Dealer_Hedging, self-trading + hedging books). Order here is the
// display order for every 三大法人 chart/table/stat in this panel.
export const INSTITUTIONAL_BUCKETS = ["foreign", "trust", "dealer"] as const;

export type InstitutionalBucket = (typeof INSTITUTIONAL_BUCKETS)[number];

const BUCKET_BY_INVESTOR: Record<string, InstitutionalBucket> = {
  Foreign_Investor: "foreign",
  Foreign_Dealer_Self: "foreign",
  Investment_Trust: "trust",
  Dealer_self: "dealer",
  Dealer_Hedging: "dealer",
};

export const BUCKET_COLORS: Record<InstitutionalBucket, string> = {
  foreign: "var(--chart-1)",
  trust: "var(--chart-2)",
  dealer: "var(--chart-3)",
};

export function bucketLabel(
  t: ReturnType<typeof useTranslations>,
  bucket: InstitutionalBucket,
): string {
  return t(`bucket.${bucket}`);
}

/** Sums `get_institutional_flow`'s 5 raw categories into the 3 display buckets, keyed by
 * `trade_date` — the one grouping pass every 三大法人 chart/table in this panel shares, so the
 * bucket totals a chart plots and a table prints can never drift apart from double
 * implementation. */
export function bucketedFlowByDate(
  rows: readonly { trade_date: string; investor: string; buy: number; sell: number }[],
): Map<string, Record<InstitutionalBucket, number>> {
  const byDate = new Map<string, Record<InstitutionalBucket, number>>();
  for (const row of rows) {
    const bucket = BUCKET_BY_INVESTOR[row.investor];
    if (!bucket) continue;
    const entry = byDate.get(row.trade_date) ?? { foreign: 0, trust: 0, dealer: 0 };
    entry[bucket] += row.buy - row.sell;
    byDate.set(row.trade_date, entry);
  }
  return byDate;
}

export const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

export function SubTabRow<T extends string>({
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
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {labelFor(tab)}
        </button>
      ))}
    </div>
  );
}

export const INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS = 30;

/** One 紅漲綠跌-colored net-flow cell (▲ red for net buy, ▼ green for net sell) — shared by every
 * 三大法人/融資券 table in this panel so the sign→color/▲▼ mapping never drifts between them.
 * `value` is always ALREADY in 張 (board lots) by the time it reaches here — callers backed by
 * raw-shares data (`institutional_flow`) divide by 1000 before passing in; callers backed by
 * data FinMind/TWSE already publish in 張 (`margin_short`'s own balance fields, confirmed against
 * TWSE's `MI_MARGN` feed, whose own column header reads "融資(交易單位)") pass the value through
 * unchanged. This keeps the 張 unit suffix rendered here honest for either source. */
export function NetFlowCell({
  value,
  locale,
}: {
  readonly value: number;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const up = value >= 0;
  return (
    <td
      className="px-2 py-1.5 text-right font-num tabular-nums"
      style={{ color: up ? "var(--up)" : "var(--down)" }}
    >
      {up ? "▲" : "▼"} {t("lotUnit", { value: formatNumber(Math.abs(value), locale) })}
    </td>
  );
}

export function SnapshotStat({
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

export function formatSignedNumber(value: number, locale: string): string {
  const sign = value >= 0 ? "▲" : "▼";
  return `${sign} ${formatNumber(Math.abs(value), locale)}`;
}
