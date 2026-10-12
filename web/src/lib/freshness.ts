/**
 * How long a read is considered fresh (TanStack Query `staleTime`), by how often the data behind
 * it actually changes. Pick the class that matches the source instead of hand-picking a number,
 * so two cards showing the same data never disagree about when to refetch. End-of-day market data
 * is additionally refreshed by the `data.updated` push (`hooks/use-data-updates.ts`) the moment
 * ingest writes new rows, so its long freshness never shows stale numbers after a close.
 */
export const FRESHNESS = {
  /** Intraday quotes and snapshots while the market is open. */
  live: 15_000,
  /** The app default: user data that other tabs/devices may change (threads, alerts, settings). */
  standard: 30_000,
  /** Things a user edits rarely but expects to see promptly elsewhere (preferences, limits). */
  slow: 60_000,
  /** Reference catalogs and end-of-day derived data (themes, screener factors, presets). */
  reference: 5 * 60_000,
  /** Effectively static per session (pet catalog, static enumerations). */
  static: 60 * 60_000,
} as const;
