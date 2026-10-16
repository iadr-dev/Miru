/**
 * US macro statistics — `/macro/us/dashboard`, `/macro/us/series`, `/macro/us/releases`,
 * `/macro/us/revisions` (`backend/src/tools/us_macro_tools.py`, all public). Descriptive context
 * only: none of these numbers is a price move, so the signed `--up`/`--down` tokens never apply
 * to anything built from these shapes (web/OVERVIEW.md §4).
 */

import { z } from "zod";

export const MACRO_GROUPS = [
  "yield_curve_recession",
  "inflation",
  "labor",
  "credit_financial_conditions",
  "policy_liquidity",
  "growth",
  "housing",
  "dollar",
  "money",
] as const;

export const macroGroupSchema = z.enum(MACRO_GROUPS);

export type MacroGroup = z.infer<typeof macroGroupSchema>;

/** The stored transformations: levels, change, change vs a year ago, % change, % change vs a
 * year ago, annualized % change. */
export const MACRO_UNITS = ["lin", "chg", "ch1", "pch", "pc1", "pca"] as const;

export const macroUnitsSchema = z.enum(MACRO_UNITS);

export type MacroUnits = z.infer<typeof macroUnitsSchema>;

export const UNIT_KINDS = ["percent", "level", "index", "count"] as const;

export const unitKindSchema = z.enum(UNIT_KINDS);

export type UnitKind = z.infer<typeof unitKindSchema>;

/** The 13 series whose first-release vs latest value history is tracked. */
export const REVISION_SERIES_IDS = [
  "PAYEMS",
  "GDPC1",
  "A191RL1Q225SBEA",
  "CPIAUCSL",
  "CPILFESL",
  "PCEPI",
  "PCEPILFE",
  "INDPRO",
  "RSAFS",
  "DGORDER",
  "UNRATE",
  "JTSJOL",
  "HOUST",
] as const;

export type RevisionSeriesId = (typeof REVISION_SERIES_IDS)[number];

export function isRevisionSeriesId(value: string): value is RevisionSeriesId {
  return (REVISION_SERIES_IDS as readonly string[]).includes(value);
}

export const macroPointSchema = z.object({
  date: z.string(),
  value: z.number(),
});

export type MacroPoint = z.infer<typeof macroPointSchema>;

// ---------------------------------------------------------------------------
// GET /macro/us/dashboard
// ---------------------------------------------------------------------------

export const dashboardSeriesSchema = z.object({
  series_id: z.string(),
  label: z.string(),
  title: z.string().nullable(),
  units: macroUnitsSchema,
  unit_kind: unitKindSchema,
  frequency: z.string(),
  higher_means: z.string(),
  higher_means_text: z.string(),
  copyrighted_third_party: z.boolean(),
  latest: macroPointSchema.nullable(),
  prior: macroPointSchema.nullable(),
  change: z.number().nullable(),
  last_observation_date: z.string().nullable(),
  release_name: z.string().nullable(),
  next_release_date: z.string().nullable(),
  sparkline: z.array(macroPointSchema).default([]),
});

export type DashboardSeries = z.infer<typeof dashboardSeriesSchema>;

export const dashboardPanelSchema = z.object({
  group: macroGroupSchema,
  label: z.string(),
  series: z.array(dashboardSeriesSchema).default([]),
});

export type DashboardPanel = z.infer<typeof dashboardPanelSchema>;

export const YIELD_CURVE_STATES = ["inverted", "flat", "positive"] as const;

export const yieldCurveStateSchema = z.enum(YIELD_CURVE_STATES);

export type YieldCurveState = z.infer<typeof yieldCurveStateSchema>;

export const yieldCurveSpreadSchema = z.object({
  series_id: z.string(),
  label: z.string(),
  latest: macroPointSchema,
  state: yieldCurveStateSchema,
  inverted_since: z.string().nullable(),
  days_inverted: z.number(),
  since_window_start: z.boolean(),
  last_inverted_date: z.string().nullable(),
});

export type YieldCurveSpread = z.infer<typeof yieldCurveSpreadSchema>;

export const recessionBandSchema = z.object({
  start: z.string(),
  end: z.string(),
  ongoing: z.boolean(),
});

export type RecessionBand = z.infer<typeof recessionBandSchema>;

export const usMacroDashboardResponseSchema = z.object({
  panels: z.array(dashboardPanelSchema).default([]),
  yield_curve: z.array(yieldCurveSpreadSchema).default([]),
  recession_bands: z.array(recessionBandSchema).default([]),
  recession_note: z.string(),
  as_of: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  currentness_caveat: z.string(),
});

export type UsMacroDashboardResponse = z.infer<typeof usMacroDashboardResponseSchema>;

// ---------------------------------------------------------------------------
// GET /macro/us/series
// ---------------------------------------------------------------------------

export const seriesMetadataSchema = z.object({
  series_id: z.string(),
  title: z.string().nullable(),
  label: z.string(),
  group: macroGroupSchema,
  group_label: z.string(),
  units: macroUnitsSchema,
  default_units: macroUnitsSchema,
  available_units: z.array(macroUnitsSchema),
  unit_kind: unitKindSchema,
  units_description: z.string().nullable(),
  frequency: z.string(),
  seasonal_adjustment: z.string().nullable(),
  last_updated: z.string().nullable(),
  notes: z.string().nullable(),
  higher_means: z.string(),
  higher_means_text: z.string(),
  copyrighted_third_party: z.boolean(),
  release_name: z.string().nullable(),
  next_release_date: z.string().nullable(),
});

export type SeriesMetadata = z.infer<typeof seriesMetadataSchema>;

export const observationRangeSchema = z.object({
  start: z.string(),
  end: z.string(),
});

export const usMacroSeriesResponseSchema = z.object({
  series_id: z.string(),
  units: macroUnitsSchema.nullable(),
  frequency: z.string().nullable(),
  points: z.array(macroPointSchema).default([]),
  metadata: seriesMetadataSchema.nullable(),
  total_observations: z.number(),
  observation_range: observationRangeSchema.nullable(),
  as_of: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  currentness_caveat: z.string(),
});

export type UsMacroSeriesResponse = z.infer<typeof usMacroSeriesResponseSchema>;

// ---------------------------------------------------------------------------
// GET /macro/us/releases
// ---------------------------------------------------------------------------

export const RELEASE_STATUSES = ["upcoming", "released"] as const;

export const releaseStatusSchema = z.enum(RELEASE_STATUSES);

export type ReleaseStatus = z.infer<typeof releaseStatusSchema>;

export const releaseSeriesSchema = z.object({
  series_id: z.string(),
  label: z.string(),
  units: macroUnitsSchema,
  unit_kind: unitKindSchema,
  latest: macroPointSchema.nullable(),
  previous: macroPointSchema.nullable(),
  change: z.number().nullable(),
  last_observation_date: z.string().nullable(),
});

export type ReleaseSeries = z.infer<typeof releaseSeriesSchema>;

export const releaseEntrySchema = z.object({
  release_id: z.number(),
  release_name: z.string(),
  release_date: z.string(),
  status: releaseStatusSchema,
  series: z.array(releaseSeriesSchema).default([]),
});

export type ReleaseEntry = z.infer<typeof releaseEntrySchema>;

export const usEconomicReleasesResponseSchema = z.object({
  from_date: z.string().nullable(),
  to_date: z.string().nullable(),
  releases: z.array(releaseEntrySchema).default([]),
  as_of: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  currentness_caveat: z.string(),
});

export type UsEconomicReleasesResponse = z.infer<typeof usEconomicReleasesResponseSchema>;

// ---------------------------------------------------------------------------
// GET /macro/us/revisions
// ---------------------------------------------------------------------------

export const revisionVintageSchema = z.object({
  as_of: z.string(),
  value: z.number(),
});

export const revisionRowSchema = z.object({
  observation_date: z.string(),
  first_release_date: z.string(),
  first_release_value: z.number(),
  latest_value: z.number(),
  latest_vintage_date: z.string(),
  revision: z.number(),
  revision_count: z.number(),
  vintages: z.array(revisionVintageSchema).default([]),
});

export type RevisionRow = z.infer<typeof revisionRowSchema>;

export const usSeriesRevisionsResponseSchema = z.object({
  series_id: z.string(),
  label: z.string().nullable(),
  units_description: z.string().nullable(),
  rows: z.array(revisionRowSchema).default([]),
  first_release_note: z.string(),
  as_of: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  currentness_caveat: z.string(),
});

export type UsSeriesRevisionsResponse = z.infer<typeof usSeriesRevisionsResponseSchema>;
