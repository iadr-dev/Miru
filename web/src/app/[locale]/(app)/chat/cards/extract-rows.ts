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
  for (const value of Object.values(record)) {
    if (Array.isArray(value) && value.length > 0) {
      return true;
    }
  }
  return false;
}

const NESTED_ENTITY_KEYS = ["bar", "snapshot", "info", "result", "profile"];

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
