"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { cardTypeLabel, fieldLabel, valueLabel } from "@/lib/field-labels";
import {
  asNumber,
  asString,
  formatCompactNumber,
  formatNumber,
  PLUMBING_KEYS,
} from "@/lib/format-number";

/** Large-magnitude monetary fields (market cap / fund size, routinely in the billions of TWD)
 * — the stat grid renders these with `formatCompactNumber` ("194.2億" style) instead of
 * `formatNumber`'s full comma-grouped digit string ("19,419,932,400"), matching how every chart
 * Y-axis in this codebase already scales large numbers (`formatCompactNumber`'s own docstring).
 * A per-symbol EPS/PER/percentage stat is NOT in this set — those stay full-precision, compact
 * notation would just be confusing at that magnitude. */
const COMPACT_NUMBER_FIELDS = new Set(["market_value", "fund_size", "fund_size_prior"]);

/** Fields to skip in the auto-derived stat grid — either structural (not a stat), already shown
 * as the card's own title/subtitle, or source/health plumbing. `best_bid`/`best_ask`(`_volume`)
 * are excluded here too — `live_quote_snapshot`'s own dedicated 五檔 depth chart below renders
 * them, so they'd otherwise show up twice (once as a raw opaque string in the grid, once parsed
 * into the chart). */
const SKIP_KEYS = new Set([
  "symbol",
  "market",
  "found",
  "missing_fields",
  "best_bid",
  "best_ask",
  "best_bid_volume",
  "best_ask_volume",
  ...PLUMBING_KEYS,
]);

type DepthTier = { price: number; volume: number };

/** TWSE MIS's `best_bid`/`best_ask`(`_volume`) fields are each a single `_`-delimited string of
 * up to 5 tiers, best (closest-to-market) tier always first; unquoted trailing tiers pad with
 * `"-"`/`"0"` rather than being omitted (confirmed live against the raw MIS endpoint:
 * `b:"2345.0000_2340.0000_..."` paired tier-for-tier with `g:"363_747_..."`). Pairs price+volume
 * per tier, keeping only tiers with a real (positive) price — never renders a placeholder tier. */
function parseDepthTiers(rawPrice: string, rawVolume: string): DepthTier[] {
  const prices = rawPrice.split("_").map(Number);
  const volumes = rawVolume.split("_").map(Number);
  const tiers: DepthTier[] = [];
  for (let i = 0; i < prices.length; i++) {
    const price = prices[i];
    if (price !== undefined && Number.isFinite(price) && price > 0) {
      const volume = volumes[i];
      tiers.push({ price, volume: volume !== undefined && Number.isFinite(volume) ? volume : 0 });
    }
  }
  return tiers;
}

/** `live_quote_snapshot`'s 五檔報價 (best-5 bid/ask depth) — two side-by-side horizontal bar
 * ladders, bar length sized by each tier's order volume (張數), matching the reference 內盤/外盤
 * layout (Screenshot_14). Bid (買方) bars are `--up` (紅), ask (賣方) bars are `--down` (綠) — the
 * one legitimate case an order-book SIDE gets 紅漲綠跌 coloring: TW retail platforms color the
 * whole bid column red / ask column green regardless of that tier's own price direction, matching
 * every reference broker UI rather than a per-tier signed price comparison. */
function FiveTierDepthChart({
  bestBid,
  bestBidVolume,
  bestAsk,
  bestAskVolume,
  t,
  locale,
}: Readonly<{
  bestBid: string;
  bestBidVolume: string;
  bestAsk: string;
  bestAskVolume: string;
  t: ReturnType<typeof useTranslations>;
  locale: string;
}>): ReactNode {
  const bids = parseDepthTiers(bestBid, bestBidVolume);
  const asks = parseDepthTiers(bestAsk, bestAskVolume);
  if (bids.length === 0 && asks.length === 0) {
    return null;
  }
  const maxVolume = Math.max(
    1,
    ...bids.map((tier) => tier.volume),
    ...asks.map((tier) => tier.volume),
  );

  return (
    <div className="mt-3 grid grid-cols-2 gap-4">
      <DepthColumn
        label={t("fiveTierBid")}
        tiers={bids}
        maxVolume={maxVolume}
        color="var(--up)"
        t={t}
        locale={locale}
        align="right"
      />
      <DepthColumn
        label={t("fiveTierAsk")}
        tiers={asks}
        maxVolume={maxVolume}
        color="var(--down)"
        t={t}
        locale={locale}
        align="left"
      />
    </div>
  );
}

function DepthColumn({
  label,
  tiers,
  maxVolume,
  color,
  t,
  locale,
  align,
}: Readonly<{
  label: string;
  tiers: DepthTier[];
  maxVolume: number;
  color: string;
  t: ReturnType<typeof useTranslations>;
  locale: string;
  align: "left" | "right";
}>): ReactNode {
  return (
    <div>
      <p className="mb-1.5 text-[11px] text-[var(--text-muted)]">{label}</p>
      <div className="flex flex-col gap-1">
        {tiers.map((tier, index) => {
          const widthPct = Math.max(4, (tier.volume / maxVolume) * 100);
          return (
            <div
              key={tier.price}
              className={cn(
                "flex items-center gap-2 text-[12px]",
                align === "right" ? "flex-row-reverse" : "flex-row",
              )}
            >
              <span className="w-14 shrink-0 font-num text-[var(--text)] tabular-nums">
                {formatNumber(tier.price, locale)}
              </span>
              <div
                className="flex min-w-0 flex-1 items-center"
                style={{ direction: align === "right" ? "rtl" : "ltr" }}
              >
                <div
                  className="h-4 rounded-[2px]"
                  style={{ width: `${widthPct}%`, backgroundColor: color }}
                  role="img"
                  aria-label={`${t("fiveTierRank", { rank: index + 1 })} ${formatNumber(tier.price, locale)}, ${formatNumber(tier.volume, locale)}`}
                />
              </div>
              <span className="w-12 shrink-0 text-right font-num text-[var(--text-muted)] tabular-nums">
                {formatNumber(tier.volume, locale)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** A single flat entity at one point in time (`price_quote`, `live_quote_snapshot`,
 * `company_profile`, `etf_fund_info`, `stock_score`) — every one of these nests its real payload
 * one level down (`bar`, `snapshot`, `info`, `result`, or the top level itself for
 * `company_profile`), so this unwraps the first present nested object before deriving stats. */
export function SnapshotCard({
  card_type,
  data,
}: Readonly<{ card_type: string; data: unknown }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");

  // `get_stock_score` (and any future cache-only read) reports `found: false` with a plain-
  // language `message` when the underlying compute hasn't run yet for this symbol — e.g. "No
  // score has been computed yet…" — rather than fabricating a stale/partial number. Without this
  // check, the raw leftover scalar fields (`profile`, `message`) would fall through to the
  // generic stat grid below and render as confusing raw key/value noise instead of an honest
  // "not yet available" note.
  if (data && typeof data === "object" && (data as Record<string, unknown>).found === false) {
    const message = (data as Record<string, unknown>).message;
    return typeof message === "string" && message.length > 0 ? (
      <p className="surface-card mt-3 p-4 text-[13px] text-[var(--text-muted)]">{message}</p>
    ) : null;
  }

  const entity = unwrapEntity(data);
  if (entity === null) {
    return null;
  }

  const symbol = asString(entity.symbol) ?? asString(entity.stock_id);
  const missing = Array.isArray(entity.missing_fields)
    ? entity.missing_fields.filter((v): v is string => typeof v === "string")
    : [];
  const stats = Object.entries(entity).filter(
    ([key, value]) =>
      !SKIP_KEYS.has(key) &&
      (typeof value === "string" || typeof value === "number" || typeof value === "boolean"),
  );
  const typeLabel = cardTypeLabel(t, card_type);

  return (
    <div className="surface-card mt-3 p-4 sm:p-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <span className="font-medium text-[13px] text-[var(--text-muted)]">
          {symbol ?? typeLabel}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">{typeLabel}</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {stats.map(([key, value]) => (
          <div key={key} className="min-w-0">
            <p className="truncate text-[11px] text-[var(--text-muted)]">{fieldLabel(t, key)}</p>
            <p
              className={
                typeof value === "number"
                  ? "font-num text-[14px] text-[var(--text)] tabular-nums"
                  : "truncate text-[14px] text-[var(--text)]"
              }
              title={
                typeof value === "number" && COMPACT_NUMBER_FIELDS.has(key)
                  ? formatNumber(asNumber(value), locale)
                  : undefined
              }
            >
              {typeof value === "number"
                ? formatNumericField(key, value, locale)
                : valueLabel(t, key, String(value))}
            </p>
          </div>
        ))}
      </div>
      {typeof entity.best_bid === "string" || typeof entity.best_ask === "string" ? (
        <FiveTierDepthChart
          bestBid={typeof entity.best_bid === "string" ? entity.best_bid : ""}
          bestBidVolume={typeof entity.best_bid_volume === "string" ? entity.best_bid_volume : ""}
          bestAsk={typeof entity.best_ask === "string" ? entity.best_ask : ""}
          bestAskVolume={typeof entity.best_ask_volume === "string" ? entity.best_ask_volume : ""}
          t={t}
          locale={locale}
        />
      ) : null}
      {missing.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {missing.map((field) => (
            <span
              key={field}
              className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]"
            >
              {t("fieldUnavailable", { field: fieldLabel(t, field) })}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const NESTED_KEYS = ["bar", "snapshot", "info", "result", "profile", "consensus", "esg"] as const;

function unwrapEntity(data: unknown): Record<string, unknown> | null {
  if (!data || typeof data !== "object") {
    return null;
  }
  const record = data as Record<string, unknown>;
  for (const key of NESTED_KEYS) {
    const nested = record[key];
    if (nested && typeof nested === "object") {
      return nested as Record<string, unknown>;
    }
  }
  // company_profile has no wrapper — its fields sit at the top level.
  return record;
}

function formatNumericField(key: string, value: number, locale: string): string {
  if (COMPACT_NUMBER_FIELDS.has(key)) return formatCompactNumber(asNumber(value), locale);
  return formatNumber(asNumber(value), locale);
}
