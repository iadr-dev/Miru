/** Shared, pure (no hooks, no React) row/entity extraction used both by the card components
 * themselves and by `RichCardView`'s pre-render check — whether a shape has *any* usable data
 * has to be decided before rendering, since a component returning `null` mid-render can't tell
 * its caller "fall back to the flat view instead" without also being a hook, which these aren't
 * (the extraction logic itself never touches React state/context, so sharing it here is safe to
 * call unconditionally, unlike calling a component function directly would be). */

const ROW_LIST_KEYS = [
  "rows",
  "hits",
  "results",
  "holders",
  "events",
  "transactions",
  "inflow_ranking",
  "outflow_ranking",
  "holdings",
  "sector_weightings",
];

/** Echoes of the CALLER's own request args, not real result rows — `etf_overlap`'s
 * `requested_symbols`/`unavailable_symbols` (`GetEtfOverlapOutput`) are always non-empty
 * whenever the request itself named symbols, even when the real result rows
 * (`overlapping_holdings`/`pairwise_overlap`) are empty. Left in the generic fallback loop below,
 * these made a genuinely-empty overlap result look "usable," routing it to `CompareTable`, which
 * then rendered nothing (no `rows`/`pairwise_overlap` to show) instead of falling back to
 * `FlatCard`'s honest raw-field display. */
const ECHO_ONLY_KEYS = new Set(["requested_symbols", "unavailable_symbols"]);

export function anyRowsPresent(data: unknown): boolean {
  if (!data || typeof data !== "object") {
    return false;
  }
  const record = data as Record<string, unknown>;
  for (const key of ROW_LIST_KEYS) {
    if (Array.isArray(record[key]) && record[key].length > 0) {
      return true;
    }
  }
  if (Array.isArray(record.pairwise_overlap) && record.pairwise_overlap.length > 0) {
    return true;
  }
  const result = record.result;
  if (result && typeof result === "object" && anyRowsPresent(result)) {
    return true;
  }
  for (const [key, value] of Object.entries(record)) {
    if (!ECHO_ONLY_KEYS.has(key) && Array.isArray(value) && value.length > 0) {
      return true;
    }
  }
  return false;
}

const NESTED_ENTITY_KEYS = ["bar", "snapshot", "info", "result", "profile", "consensus", "esg"];

export function hasSnapshotEntity(data: unknown): boolean {
  if (!data || typeof data !== "object") {
    return false;
  }
  const record = data as Record<string, unknown>;
  for (const key of NESTED_ENTITY_KEYS) {
    if (record[key] && typeof record[key] === "object") {
      return true;
    }
  }
  // company_profile has no wrapper — presence of any scalar field at the top level counts.
  return Object.values(record).some(
    (v) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  );
}
