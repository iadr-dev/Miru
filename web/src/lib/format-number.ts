/** Shared, defensive value/row extraction for rich cards — `card.data` is an open
 * `Record<string, unknown>` (the backend's `RichCard.data`, not a typed contract on this side),
 * so every accessor here narrows without throwing: a missing/malformed field renders as absent,
 * never crashes the turn. */

/** Governance/provenance plumbing every `RichCard.data` carries alongside its real fields
 * (`agent/loop.py::_build_card` stamps `non_trading_caveat`; the source layer's own
 * `source_id`/`currentness_caveat`/`health_status` ride along on most tool outputs) — never the
 * user-facing content a card exists to show, so every generic field-listing renderer excludes
 * these rather than displaying internal source/health bookkeeping as if it were a data point. */
export const PLUMBING_KEYS = new Set([
  "source_id",
  "health_status",
  "currentness_caveat",
  "non_trading_caveat",
  "sector_caveat",
  "dividend_caveat",
  // Prose explaining why a sibling field is `null` (e.g. `premium_discount_unavailable_reason`
  // alongside `etf_fund_info`'s `premium_discount_pct`) — the skill's own instructions already
  // tell the model to surface that prose in the answer text, so a raw table/stat-grid rendering
  // of it would just duplicate an unlabeled English sentence as a table cell.
  "premium_discount_unavailable_reason",
]);

export function asArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is Record<string, unknown> => typeof item === "object" && item !== null,
  );
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** The first present date-like field across the common backend names (`trade_date`, `date`,
 * `period_end`, `announced_date`, ...) — rows use whichever their domain calls it. */
const DATE_KEYS = [
  "trade_date",
  "date",
  "period_end",
  "announced_date",
  "created_date",
  "report_date",
] as const;

export function rowDate(row: Record<string, unknown>): string | null {
  for (const key of DATE_KEYS) {
    const value = asString(row[key]);
    if (value) {
      return value;
    }
  }
  return null;
}

/** Formats a number through `Intl.NumberFormat` for the active locale — never a hand-rolled
 * `toFixed`/string-concat. Callers still wrap the result in an element with `font-num
 * tabular-nums` classes; this only handles the numeric formatting itself. */
export function formatNumber(
  value: number | null,
  locale: string,
  options?: { decimals?: number },
): string {
  if (value === null) {
    return "—";
  }
  const decimals = options?.decimals;
  return new Intl.NumberFormat(
    locale,
    decimals !== undefined
      ? { minimumFractionDigits: decimals, maximumFractionDigits: decimals }
      : undefined,
  ).format(value);
}

/** Compact axis-tick formatting (e.g. "4.7億"/"4.7B") for large-magnitude series (monthly
 * revenue, institutional-flow shares, chip trading value) — a chart Y-axis at its default fixed
 * width clips a full `formatNumber` rendering of a hundreds-of-billions value (the tick literally
 * gets cut off mid-digit), whereas `Tooltip`/the data-sheet table below the chart still show the
 * exact full number via `formatNumber` — this is axis-label-only compaction, never a lossy
 * display of the real value anywhere else. */
export function formatCompactNumber(value: number | null, locale: string): string {
  if (value === null) {
    return "—";
  }
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}
