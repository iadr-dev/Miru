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
