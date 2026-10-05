const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** Parse a timestamp from the API. Every stored timestamp is UTC, but the dev (SQLite) backend
 * serialises it WITHOUT a zone suffix (`2026-10-03T12:24:12`), and `new Date()` reads a zone-less
 * date-time as the browser's local time — shifting every value by the viewer's offset. Label such
 * a value UTC; values that already carry a zone (Postgres) and date-only strings pass through. */
export function parseServerDate(value: string): Date {
  const needsZone = value.includes("T") && !HAS_ZONE.test(value);
  return new Date(needsZone ? `${value}Z` : value);
}
