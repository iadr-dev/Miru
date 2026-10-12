/**
 * Sector views — `/market/sector-heatmap`, `/market/sector-constituents`,
 * `/market/sector-rotation-rrg`, `/market/stock-heatmap`.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// 14b. GET /market/sector-heatmap — get_sector_heatmap (public, 產業別漲跌熱力圖)
// ---------------------------------------------------------------------------

export const SECTOR_HEATMAP_PERIODS = [
  "immediate",
  "week",
  "two_weeks",
  "month",
  "quarter",
  "half_year",
  "year",
] as const;

export const sectorHeatmapPeriodSchema = z.enum(SECTOR_HEATMAP_PERIODS);

export type SectorHeatmapPeriod = z.infer<typeof sectorHeatmapPeriodSchema>;

export const SECTOR_HEATMAP_SIZE_BY_OPTIONS = ["turnover", "market_value", "volume"] as const;

export const sectorHeatmapSizeBySchema = z.enum(SECTOR_HEATMAP_SIZE_BY_OPTIONS);

export type SectorHeatmapSizeBy = z.infer<typeof sectorHeatmapSizeBySchema>;

export const sectorHeatmapRowSchema = z.object({
  industry: z.string(),
  avg_pct_change: z.number(),
  stock_count: z.number(),
  advancing_count: z.number(),
  declining_count: z.number(),
  total_turnover: z.number().nullable(),
  weighted_by_turnover: z.boolean(),
  total_volume: z.number(),
  total_market_value: z.number().nullable(),
});

export type SectorHeatmapRow = z.infer<typeof sectorHeatmapRowSchema>;

export const sectorHeatmapDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  period: sectorHeatmapPeriodSchema.default("immediate"),
  size_by: sectorHeatmapSizeBySchema.default("turnover"),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  industries: z.array(sectorHeatmapRowSchema).default([]),
});

export type SectorHeatmapData = z.infer<typeof sectorHeatmapDataSchema>;

export const sectorHeatmapResponseSchema = sectorHeatmapDataSchema;

export type SectorHeatmapResponse = z.infer<typeof sectorHeatmapResponseSchema>;

// ---------------------------------------------------------------------------
// 14c. GET /market/sector-constituents — get_sector_constituents (public, per-stock drill-down)
// ---------------------------------------------------------------------------

export const sectorConstituentRowSchema = z.object({
  symbol: z.string(),
  stock_name: z.string(),
  industry: z.string(),
  pct_change: z.number(),
  close: z.number(),
  volume: z.number(),
  turnover: z.number().nullable(),
});

export type SectorConstituentRow = z.infer<typeof sectorConstituentRowSchema>;

export const sectorConstituentsDataSchema = z.object({
  market: z.string(),
  industry: z.string().nullable().optional(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(sectorConstituentRowSchema).default([]),
});

export type SectorConstituentsData = z.infer<typeof sectorConstituentsDataSchema>;

export const sectorConstituentsResponseSchema = sectorConstituentsDataSchema;

export type SectorConstituentsResponse = z.infer<typeof sectorConstituentsResponseSchema>;

// ---------------------------------------------------------------------------
// 14d-2. GET /market/sector-rotation-rrg — get_sector_rotation_rrg (public, real weekly RRG)
// ---------------------------------------------------------------------------

export const sectorRrgWeekPointSchema = z.object({
  week_ending: z.string(),
  rs_ratio: z.number(),
  rs_momentum: z.number(),
  weekly_change_pct: z.number(),
});

export type SectorRrgWeekPoint = z.infer<typeof sectorRrgWeekPointSchema>;

export const sectorRrgSeriesSchema = z.object({
  industry_id: z.string(),
  industry_name: z.string(),
  weight: z.number(),
  points: z.array(sectorRrgWeekPointSchema).default([]),
});

export type SectorRrgSeries = z.infer<typeof sectorRrgSeriesSchema>;

export const sectorRotationRrgDataSchema = z.object({
  market: z.string(),
  benchmark_id: z.string(),
  benchmark_name: z.string(),
  week_endings: z.array(z.string()).default([]),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  sectors: z.array(sectorRrgSeriesSchema).default([]),
});

export type SectorRotationRrgData = z.infer<typeof sectorRotationRrgDataSchema>;

export const sectorRotationRrgResponseSchema = sectorRotationRrgDataSchema;

export type SectorRotationRrgResponse = z.infer<typeof sectorRotationRrgResponseSchema>;

// ---------------------------------------------------------------------------
// 14e. GET /market/stock-heatmap — get_stock_heatmap (public, flat per-stock 股票熱力圖)
// ---------------------------------------------------------------------------

export const stockHeatmapRowSchema = z.object({
  symbol: z.string(),
  stock_name: z.string(),
  industry: z.string(),
  pct_change: z.number(),
  close: z.number(),
  turnover: z.number().nullable(),
  volume: z.number(),
  market_value: z.number().nullable(),
});

export type StockHeatmapRow = z.infer<typeof stockHeatmapRowSchema>;

export const stockHeatmapDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  period: sectorHeatmapPeriodSchema.default("immediate"),
  size_by: sectorHeatmapSizeBySchema.default("turnover"),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  stocks: z.array(stockHeatmapRowSchema).default([]),
});

export type StockHeatmapData = z.infer<typeof stockHeatmapDataSchema>;

export const stockHeatmapResponseSchema = stockHeatmapDataSchema;

export type StockHeatmapResponse = z.infer<typeof stockHeatmapResponseSchema>;
