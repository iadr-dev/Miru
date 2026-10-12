/**
 * 技術分析 — `/stock/technical-indicators`.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// 21. GET /stock/technical-indicators — get_technical_indicators (public). Mirrors
//     `GetTechnicalIndicatorsOutput`/`analytics/models.py`'s indicator-series models field-for-
//     field. 技術分析's K線 chart overlay/subpane data source.
// ---------------------------------------------------------------------------

export const smaResultSchema = z.object({
  window: z.number(),
  values: z.array(z.number().nullable()).default([]),
});

export const movingAverageBundleSchema = z.object({
  ma5: smaResultSchema,
  ma10: smaResultSchema,
  ma20: smaResultSchema,
  ma60: smaResultSchema,
  ma120: smaResultSchema,
  ma240: smaResultSchema,
});

export type MovingAverageBundle = z.infer<typeof movingAverageBundleSchema>;

export const bollingerResultSchema = z.object({
  window: z.number(),
  mid: z.array(z.number().nullable()).default([]),
  upper: z.array(z.number().nullable()).default([]),
  lower: z.array(z.number().nullable()).default([]),
});

export type BollingerResult = z.infer<typeof bollingerResultSchema>;

export const kdResultSchema = z.object({
  period: z.number(),
  k: z.array(z.number().nullable()).default([]),
  d: z.array(z.number().nullable()).default([]),
});

export type KdResult = z.infer<typeof kdResultSchema>;

export const macdResultSchema = z.object({
  dif: z.array(z.number().nullable()).default([]),
  dem: z.array(z.number().nullable()).default([]),
  histogram: z.array(z.number().nullable()).default([]),
});

export type MacdResult = z.infer<typeof macdResultSchema>;

export const supportResistanceLevelSchema = z.object({
  kind: z.string(),
  price: z.number(),
  bar_index: z.number(),
});

export type SupportResistanceLevel = z.infer<typeof supportResistanceLevelSchema>;

export const supportResistanceResultSchema = z.object({
  levels: z.array(supportResistanceLevelSchema).default([]),
});

export type SupportResistanceResult = z.infer<typeof supportResistanceResultSchema>;

export const candlestickPatternHitSchema = z.object({
  bar_index: z.number(),
  pattern: z.string(),
  direction: z.number(),
});

export type CandlestickPatternHit = z.infer<typeof candlestickPatternHitSchema>;

export const eodBarSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
  turnover: z.number().nullable().optional(),
});

export type EodBar = z.infer<typeof eodBarSchema>;

export const technicalIndicatorsResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  bars: z.array(eodBarSchema).default([]),
  trade_dates: z.array(z.string()).default([]),
  moving_averages: movingAverageBundleSchema.nullable().optional(),
  bollinger: bollingerResultSchema.nullable().optional(),
  kd: kdResultSchema.nullable().optional(),
  macd: macdResultSchema.nullable().optional(),
  support_resistance: supportResistanceResultSchema.nullable().optional(),
  candlestick_patterns: z.array(candlestickPatternHitSchema).default([]),
  non_trading_caveat: z.string(),
});

export type TechnicalIndicatorsResponse = z.infer<typeof technicalIndicatorsResponseSchema>;
