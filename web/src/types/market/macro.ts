/**
 * Macro — `/macro`, `/market/macro-calendar`
 * (`backend/src/api/routers/market_rest.py`).
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// 7. GET /macro — get_macro_indicators (whole tool Sponsor-tier)
// ---------------------------------------------------------------------------

export const exchangeRateRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  cash_buy: z.number(),
  cash_sell: z.number(),
  spot_buy: z.number(),
  spot_sell: z.number(),
});

export type ExchangeRateRow = z.infer<typeof exchangeRateRowSchema>;

export const centralBankRateRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  date: z.string(),
  country: z.string(),
  full_country_name: z.string(),
  interest_rate: z.number(),
});

export type CentralBankRateRow = z.infer<typeof centralBankRateRowSchema>;

export const goldPriceRowSchema = z.object({
  market: z.string(),
  date: z.string(),
  price: z.number(),
});

export type GoldPriceRow = z.infer<typeof goldPriceRowSchema>;

export const crudeOilPricesRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  date: z.string(),
  price: z.number(),
});

export type CrudeOilPricesRow = z.infer<typeof crudeOilPricesRowSchema>;

export const governmentBondYieldRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  date: z.string(),
  value: z.number(),
});

export type GovernmentBondYieldRow = z.infer<typeof governmentBondYieldRowSchema>;

export const fearGreedIndexRowSchema = z.object({
  market: z.string(),
  date: z.string(),
  fear_greed: z.number(),
  fear_greed_emotion: z.string(),
});

export type FearGreedIndexRow = z.infer<typeof fearGreedIndexRowSchema>;

export const macroIndicatorsDataSchema = z.object({
  currentness_caveat: z.string(),
  exchange_rate: z.array(exchangeRateRowSchema).default([]),
  central_bank_rate: z.array(centralBankRateRowSchema).default([]),
  gold_price: z.array(goldPriceRowSchema).default([]),
  crude_oil_prices: z.array(crudeOilPricesRowSchema).default([]),
  government_bond_yield: z.array(governmentBondYieldRowSchema).default([]),
  fear_greed_index: z.array(fearGreedIndexRowSchema).default([]),
});

export type MacroIndicatorsData = z.infer<typeof macroIndicatorsDataSchema>;

// `get_macro_indicators` is public (the Sponsor-only Fear & Greed Index is left empty for a free
// caller), so the response is never a masked teaser.
export const macroIndicatorsResponseSchema = macroIndicatorsDataSchema;

export type MacroIndicatorsResponse = z.infer<typeof macroIndicatorsResponseSchema>;

// ---------------------------------------------------------------------------
// 7b. GET /market/macro-calendar — get_macro_calendar (public, financecalendar.com)
// ---------------------------------------------------------------------------

export const MACRO_IMPACT_LEVELS = ["high", "medium", "low"] as const;

export const macroImpactLevelSchema = z.enum(MACRO_IMPACT_LEVELS);

export type MacroImpactLevel = z.infer<typeof macroImpactLevelSchema>;

export const macroReleaseEventRowSchema = z.object({
  market: z.string(),
  date: z.string(),
  time_utc: z.string().nullable(),
  name: z.string(),
  title: z.string(),
  impact: z.string(),
  category: z.string(),
  consensus: z.string().nullable(),
  prior: z.string().nullable(),
  actual: z.string().nullable(),
  url: z.string().nullable(),
});

export type MacroReleaseEventRow = z.infer<typeof macroReleaseEventRowSchema>;

export const macroCalendarResponseSchema = z.object({
  currentness_caveat: z.string(),
  events: z.array(macroReleaseEventRowSchema).default([]),
});

export type MacroCalendarResponse = z.infer<typeof macroCalendarResponseSchema>;
