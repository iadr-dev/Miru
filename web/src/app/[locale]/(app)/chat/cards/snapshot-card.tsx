"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import {
  asNumber,
  asString,
  formatNumber,
  PLUMBING_KEYS,
} from "@/app/[locale]/(app)/chat/cards/card-format";

/** Fields to skip in the auto-derived stat grid — either structural (not a stat), already shown
 * as the card's own title/subtitle, or source/health plumbing. */
const SKIP_KEYS = new Set(["symbol", "market", "found", "missing_fields", ...PLUMBING_KEYS]);

/** A single flat entity at one point in time (`price_quote`, `live_quote_snapshot`,
 * `company_profile`, `etf_fund_info`, `stock_score`) — every one of these nests its real payload
 * one level down (`bar`, `snapshot`, `info`, `result`, or the top level itself for
 * `company_profile`), so this unwraps the first present nested object before deriving stats. */
export function SnapshotCard({ card_type, data }: { card_type: string; data: unknown }): ReactNode {
  const locale = useLocale();
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

  return (
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <span className="font-medium text-[13px] text-[var(--text-muted)]">
          {symbol ?? card_type}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">{card_type}</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {stats.map(([key, value]) => (
          <div key={key} className="min-w-0">
            <p className="truncate text-[11px] text-[var(--text-muted)]">{key}</p>
            <p
              className={
                typeof value === "number"
                  ? "font-num text-[14px] text-[var(--text)] tabular-nums"
                  : "truncate text-[14px] text-[var(--text)]"
              }
            >
              {typeof value === "number" ? formatNumber(asNumber(value), locale) : String(value)}
            </p>
          </div>
        ))}
      </div>
      {missing.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {missing.map((field) => (
            <span
              key={field}
              className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]"
            >
              {field} unavailable
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const NESTED_KEYS = ["bar", "snapshot", "info", "result", "profile"] as const;

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
