/**
 * Market-dashboard contracts — the 6 (+1 stretch) real endpoints under
 * `backend/src/api/routers/market_rest.py` (base path `/api/v1`): institutional-ranking,
 * margin-maintenance, day-trading, etf-money-flow, industry-flow, derivatives, macro,
 * macro/business-indicator. Every route is a thin projection of a `tools/*.py` tool dispatched
 * through `tools.base.dispatch_tool`, so every Sponsor-tier row model here is unioned with
 * `maskedTeaserSchema` — an unentitled caller gets that shape back at HTTP 200, never an error
 * (web/CLAUDE.md §4/§8). Row field names mirror `backend/src/schemas/capabilities.py` exactly;
 * a field renamed there is a compile error here, not a silent runtime mismatch.
 */
import { z } from "zod";
import { TIERS } from "@/types/account";

// ---------------------------------------------------------------------------
// Shared envelope pieces
// ---------------------------------------------------------------------------

/** `tools/base.py::MaskedTeaser` — the Sponsor-tier lock envelope. Always HTTP 200. */
export const maskedTeaserSchema = z.object({
  locked: z.literal(true),
  required_tier: z.enum(TIERS.filter((t) => t !== "free") as ["premium", "pro"]),
  preview: z.string(),
  tool_name: z.string(),
});
export type MaskedTeaser = z.infer<typeof maskedTeaserSchema>;

export const HEALTH_STATUSES = ["ok", "stale", "unreachable", "unavailable-tier"] as const;
export const healthStatusSchema = z.enum(HEALTH_STATUSES);

/** The `_RowsOutput` shape every non-bundled tool output subclasses (`source_id` +
 * `currentness_caveat` + `health_status` + `rows`). */
function rowsOutputSchema<RowSchema extends z.ZodTypeAny>(rowSchema: RowSchema) {
  return z.object({
    source_id: z.string(),
    currentness_caveat: z.string(),
    health_status: healthStatusSchema,
    rows: z.array(rowSchema).default([]),
  });
}

// ---------------------------------------------------------------------------
// 1. GET /market/institutional-ranking — get_institutional_flow_total_market (public)
// ---------------------------------------------------------------------------

export const institutionalFlowTotalMarketRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  name: z.string(),
  buy: z.number(),
  sell: z.number(),
});
export type InstitutionalFlowTotalMarketRow = z.infer<typeof institutionalFlowTotalMarketRowSchema>;

export const institutionalRankingResponseSchema = rowsOutputSchema(
  institutionalFlowTotalMarketRowSchema,
);
export type InstitutionalRankingResponse = z.infer<typeof institutionalRankingResponseSchema>;

// ---------------------------------------------------------------------------
// 2. GET /market/margin-maintenance — get_margin_maintenance (Sponsor-tier)
// ---------------------------------------------------------------------------

export const marginMaintenanceRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  total_exchange_margin_maintenance: z.number(),
});
export type MarginMaintenanceRow = z.infer<typeof marginMaintenanceRowSchema>;

export const marginMaintenanceDataSchema = rowsOutputSchema(marginMaintenanceRowSchema);
export const marginMaintenanceResponseSchema = z.union([
  marginMaintenanceDataSchema,
  maskedTeaserSchema,
]);
export type MarginMaintenanceResponse = z.infer<typeof marginMaintenanceResponseSchema>;

// ---------------------------------------------------------------------------
// 3. GET /market/day-trading — get_day_trading (public, per-symbol)
// ---------------------------------------------------------------------------

export const dayTradingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  buy_after_sale: z.string(),
  volume: z.number(),
  buy_amount: z.number(),
  sell_amount: z.number(),
});
export type DayTradingRow = z.infer<typeof dayTradingRowSchema>;

export const dayTradingResponseSchema = rowsOutputSchema(dayTradingRowSchema);
export type DayTradingResponse = z.infer<typeof dayTradingResponseSchema>;

// ---------------------------------------------------------------------------
// 4. GET /market/etf-money-flow — get_etf_money_flow (Sponsor-tier)
// ---------------------------------------------------------------------------

export const etfMoneyFlowRowSchema = z.object({
  component_stock_id: z.string(),
  component_stock_name: z.string(),
  active_etf_holder_count: z.number().nullable().optional(),
  held_shares: z.number().nullable().optional(),
  held_market_value: z.number().nullable().optional(),
  stock_shares_held_pct: z.number().nullable().optional(),
  net_shares_flow: z.number(),
  buying_etf_count: z.number(),
  selling_etf_count: z.number(),
});
export type EtfMoneyFlowRow = z.infer<typeof etfMoneyFlowRowSchema>;

export const etfMoneyFlowDataSchema = z.object({
  market: z.string(),
  start_date: z.string(),
  end_date: z.string(),
  holding_as_of: z.string().nullable().optional(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  total_stock_count: z.number().default(0),
  stock_shares_held_pct_caveat: z.string(),
  inflow_ranking: z.array(etfMoneyFlowRowSchema).default([]),
  outflow_ranking: z.array(etfMoneyFlowRowSchema).default([]),
});
export const etfMoneyFlowResponseSchema = z.union([etfMoneyFlowDataSchema, maskedTeaserSchema]);
export type EtfMoneyFlowResponse = z.infer<typeof etfMoneyFlowResponseSchema>;

// ---------------------------------------------------------------------------
// 5. GET /market/industry-flow — get_industry_money_flow (Sponsor-tier)
// ---------------------------------------------------------------------------

export const industryChainMoneyFlowRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  industry: z.string(),
  sub_industry: z.string(),
  stock_count: z.number(),
  trading_volume: z.number(),
  trading_money: z.number(),
  trading_money_pct: z.number(),
});
export type IndustryChainMoneyFlowRow = z.infer<typeof industryChainMoneyFlowRowSchema>;

export const industryFlowDataSchema = rowsOutputSchema(industryChainMoneyFlowRowSchema);
export const industryFlowResponseSchema = z.union([industryFlowDataSchema, maskedTeaserSchema]);
export type IndustryFlowResponse = z.infer<typeof industryFlowResponseSchema>;

// ---------------------------------------------------------------------------
// 6. GET /derivatives/{contract} — bundles get_futures_options_daily (public),
//    get_put_call_ratio (public), get_derivatives_institutional (Sponsor-tier)
// ---------------------------------------------------------------------------

export const futuresDailyRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  contract_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  spread: z.number(),
  spread_per: z.number(),
  volume: z.number(),
  settlement_price: z.number(),
  open_interest: z.number(),
  trading_session: z.string(),
});
export type FuturesDailyRow = z.infer<typeof futuresDailyRowSchema>;

export const optionDailyRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  contract_date: z.string(),
  strike_price: z.number(),
  call_put: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
  settlement_price: z.number(),
  open_interest: z.number(),
  trading_session: z.string(),
});
export type OptionDailyRow = z.infer<typeof optionDailyRowSchema>;

export const futuresOptionsDailyDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  futures: z.array(futuresDailyRowSchema).default([]),
  options: z.array(optionDailyRowSchema).default([]),
});
export type FuturesOptionsDailyData = z.infer<typeof futuresOptionsDailyDataSchema>;

export const putCallRatioDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  options: z.array(optionDailyRowSchema).default([]),
});
export type PutCallRatioData = z.infer<typeof putCallRatioDataSchema>;

export const futuresInstitutionalFlowRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  investor: z.string(),
  long_deal_volume: z.number(),
  long_deal_amount: z.number(),
  short_deal_volume: z.number(),
  short_deal_amount: z.number(),
  long_open_interest_balance_volume: z.number(),
  long_open_interest_balance_amount: z.number(),
  short_open_interest_balance_volume: z.number(),
  short_open_interest_balance_amount: z.number(),
});
export type FuturesInstitutionalFlowRow = z.infer<typeof futuresInstitutionalFlowRowSchema>;

export const optionInstitutionalFlowRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  call_put: z.string(),
  investor: z.string(),
  long_deal_volume: z.number(),
  long_deal_amount: z.number(),
  short_deal_volume: z.number(),
  short_deal_amount: z.number(),
  long_open_interest_balance_volume: z.number(),
  long_open_interest_balance_amount: z.number(),
  short_open_interest_balance_volume: z.number(),
  short_open_interest_balance_amount: z.number(),
});
export type OptionInstitutionalFlowRow = z.infer<typeof optionInstitutionalFlowRowSchema>;

const largeTradersFieldsSchema = z.object({
  buy_top5_trader_open_interest: z.number(),
  buy_top5_trader_open_interest_per: z.number(),
  buy_top10_trader_open_interest: z.number(),
  buy_top10_trader_open_interest_per: z.number(),
  sell_top5_trader_open_interest: z.number(),
  sell_top5_trader_open_interest_per: z.number(),
  sell_top10_trader_open_interest: z.number(),
  sell_top10_trader_open_interest_per: z.number(),
  market_open_interest: z.number(),
  buy_top5_specific_open_interest: z.number(),
  buy_top5_specific_open_interest_per: z.number(),
  buy_top10_specific_open_interest: z.number(),
  buy_top10_specific_open_interest_per: z.number(),
  sell_top5_specific_open_interest: z.number(),
  sell_top5_specific_open_interest_per: z.number(),
  sell_top10_specific_open_interest: z.number(),
  sell_top10_specific_open_interest_per: z.number(),
});

export const futuresOiLargeTradersRowSchema = z
  .object({
    symbol: z.string(),
    market: z.string(),
    trade_date: z.string(),
    name: z.string(),
    contract_type: z.string(),
  })
  .merge(largeTradersFieldsSchema);
export type FuturesOiLargeTradersRow = z.infer<typeof futuresOiLargeTradersRowSchema>;

export const optionOiLargeTradersRowSchema = z
  .object({
    symbol: z.string(),
    market: z.string(),
    trade_date: z.string(),
    put_call: z.string(),
    name: z.string(),
    contract_type: z.string(),
  })
  .merge(largeTradersFieldsSchema);
export type OptionOiLargeTradersRow = z.infer<typeof optionOiLargeTradersRowSchema>;

export const derivativesInstitutionalDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  futures: z.array(futuresInstitutionalFlowRowSchema).default([]),
  options: z.array(optionInstitutionalFlowRowSchema).default([]),
  futures_oi_large_traders: z.array(futuresOiLargeTradersRowSchema).default([]),
  option_oi_large_traders: z.array(optionOiLargeTradersRowSchema).default([]),
});
export type DerivativesInstitutionalData = z.infer<typeof derivativesInstitutionalDataSchema>;
export const derivativesInstitutionalResponseSchema = z.union([
  derivativesInstitutionalDataSchema,
  maskedTeaserSchema,
]);
export type DerivativesInstitutionalResponse = z.infer<
  typeof derivativesInstitutionalResponseSchema
>;

/** `GET /derivatives/{contract}` bundles all three above; only `institutional` can be masked. */
export const derivativesResponseSchema = z.object({
  futures_options_daily: futuresOptionsDailyDataSchema,
  put_call_ratio: putCallRatioDataSchema,
  institutional: derivativesInstitutionalResponseSchema,
});
export type DerivativesResponse = z.infer<typeof derivativesResponseSchema>;

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
export const macroIndicatorsResponseSchema = z.union([
  macroIndicatorsDataSchema,
  maskedTeaserSchema,
]);
export type MacroIndicatorsResponse = z.infer<typeof macroIndicatorsResponseSchema>;

// ---------------------------------------------------------------------------
// 8. GET /macro/business-indicator — get_business_indicator (Sponsor-tier)
// ---------------------------------------------------------------------------

export const businessIndicatorRowSchema = z.object({
  market: z.string(),
  date: z.string(),
  leading: z.number(),
  leading_notrend: z.number(),
  coincident: z.number(),
  coincident_notrend: z.number(),
  lagging: z.number(),
  lagging_notrend: z.number(),
  monitoring: z.number(),
  monitoring_color: z.string(),
});
export type BusinessIndicatorRow = z.infer<typeof businessIndicatorRowSchema>;

/** `macro_tools.py` defines its own `_RowsOutput` (currentness_caveat + rows only — no
 * `source_id`/`health_status`), distinct from `chip_tools.py`'s richer envelope used by every
 * other Sponsor row-list above — mirrored exactly, not the generic `rowsOutputSchema` helper. */
export const businessIndicatorDataSchema = z.object({
  currentness_caveat: z.string(),
  rows: z.array(businessIndicatorRowSchema).default([]),
});
export const businessIndicatorResponseSchema = z.union([
  businessIndicatorDataSchema,
  maskedTeaserSchema,
]);
export type BusinessIndicatorResponse = z.infer<typeof businessIndicatorResponseSchema>;

/** Narrows a Sponsor-tier union response down to the masked-teaser branch, if present — every
 * card component branches on this once rather than re-deriving the `locked` check per field. */
export function isMaskedTeaser(value: { locked?: true } | object): value is MaskedTeaser {
  return "locked" in value && value.locked === true;
}

// ---------------------------------------------------------------------------
// 9. GET /market/symbol-info + /market/price-history — the shared batched
//    per-symbol enrichment `useSymbolQuotes` reads (public).
// ---------------------------------------------------------------------------

export const symbolInfoRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  name: z.string(),
  industry_category: z.string().nullable(),
});
export type SymbolInfoRow = z.infer<typeof symbolInfoRowSchema>;

// `GetSymbolInfoOutput`/`GetPriceHistoryOutput` are `{market, ..., data_available,
// unavailable_reason, rows}` — a distinct shape from `rowsOutputSchema`'s
// `{source_id, currentness_caveat, health_status, rows}` (those two backend tools never route
// through a `SourceResult`, they read the ingest-owned Parquet store directly).
export const symbolInfoResponseSchema = z.object({
  market: z.string(),
  roster_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(symbolInfoRowSchema).default([]),
});
export type SymbolInfoResponse = z.infer<typeof symbolInfoResponseSchema>;

export const priceHistoryBarSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
});
export type PriceHistoryBar = z.infer<typeof priceHistoryBarSchema>;

export const priceHistoryResponseSchema = z.object({
  market: z.string(),
  start_date: z.string().nullable(),
  end_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(priceHistoryBarSchema).default([]),
});
export type PriceHistoryResponse = z.infer<typeof priceHistoryResponseSchema>;

// ---------------------------------------------------------------------------
// 10. GET /analytics/rankings — get_score_leaderboard (public composite AI score)
// ---------------------------------------------------------------------------

export const SCORE_BANDS = ["偏多", "中性", "偏空"] as const;
export const scoreBandSchema = z.enum(SCORE_BANDS);
export type ScoreBand = z.infer<typeof scoreBandSchema>;

export const LEADERBOARD_DIRECTIONS = ["bullish", "bearish"] as const;
export const leaderboardDirectionSchema = z.enum(LEADERBOARD_DIRECTIONS);
export type LeaderboardDirection = z.infer<typeof leaderboardDirectionSchema>;

export const LEADERBOARD_UNIVERSES = ["stock", "etf"] as const;
export const leaderboardUniverseSchema = z.enum(LEADERBOARD_UNIVERSES);
export type LeaderboardUniverse = z.infer<typeof leaderboardUniverseSchema>;

export const SYMBOL_TYPES = ["stock", "etf", "etf_leveraged", "etf_inverse"] as const;
export const symbolTypeSchema = z.enum(SYMBOL_TYPES);
export type SymbolType = z.infer<typeof symbolTypeSchema>;

/** One named factor's cross-sectional standing — `percentile`/`unavailable_reason` are mutually
 * exclusive (a factor missing its inputs reports why, never a fabricated percentile). */
export const factorScoreSchema = z.object({
  name: z.string(),
  percentile: z.number().nullable(),
  description: z.string(),
  unavailable_reason: z.string().nullable(),
});
export type FactorScore = z.infer<typeof factorScoreSchema>;

/** Every per-factor key is optional — a stock and an ETF are scored on disjoint factor sets
 * (`analytics/score.py`'s `StockScoreBreakdown`/`EtfScoreBreakdown`), so a result of one
 * `symbol_type` simply omits the other universe's keys rather than sending a fabricated one. */
export const scoreBreakdownSchema = z.object({
  momentum: factorScoreSchema.optional(),
  trend_quality: factorScoreSchema.optional(),
  value_guard: factorScoreSchema.optional(),
  quality: factorScoreSchema.optional(),
  revenue_flow: factorScoreSchema.optional(),
  premium_discount: factorScoreSchema.optional(),
  cost: factorScoreSchema.optional(),
  management_activity: factorScoreSchema.optional(),
  liquidity_flow: factorScoreSchema.optional(),
});
export type ScoreBreakdown = z.infer<typeof scoreBreakdownSchema>;

export const scoreResultSchema = z.object({
  symbol: z.string(),
  symbol_type: symbolTypeSchema.nullable().optional(),
  composite: z.number().nullable(),
  band: scoreBandSchema,
  breakdown: scoreBreakdownSchema.nullable().optional(),
  partial_factors: z.array(z.string()).optional(),
  regime_caveat: z.string().optional(),
  non_trading_caveat: z.string().optional(),
  normalization_caveat: z.string().optional(),
});
export type ScoreResult = z.infer<typeof scoreResultSchema>;

export const scoreLeaderboardDataSchema = z.object({
  market: z.string(),
  as_of: z.string().nullable(),
  results: z.array(scoreResultSchema).default([]),
});
export type ScoreLeaderboardData = z.infer<typeof scoreLeaderboardDataSchema>;

// ---------------------------------------------------------------------------
// 11. GET /market/price-momentum — get_price_momentum (public single-factor ranking)
// ---------------------------------------------------------------------------

export const MOMENTUM_PERIODS = ["day", "week", "month"] as const;
export const momentumPeriodSchema = z.enum(MOMENTUM_PERIODS);
export type MomentumPeriod = z.infer<typeof momentumPeriodSchema>;

export const priceMomentumRowSchema = z.object({
  symbol: z.string(),
  stock_name: z.string(),
  pct_change: z.number(),
});
export type PriceMomentumRow = z.infer<typeof priceMomentumRowSchema>;

export const priceMomentumDataSchema = z.object({
  market: z.string(),
  period: momentumPeriodSchema,
  trade_date: z.string().nullable(),
  period_ago_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  top_gainers: z.array(priceMomentumRowSchema).default([]),
  top_losers: z.array(priceMomentumRowSchema).default([]),
});
export type PriceMomentumData = z.infer<typeof priceMomentumDataSchema>;
// Public, single-factor ranking — never Sponsor-masked (unlike `holderAccumulationResponseSchema`
// below), matching `market-panel.tsx`'s own un-branched render of this query's data.
export const priceMomentumResponseSchema = priceMomentumDataSchema;
export type PriceMomentumResponse = z.infer<typeof priceMomentumResponseSchema>;

// ---------------------------------------------------------------------------
// 12. GET /market/holder-accumulation — get_holder_accumulation (Sponsor-tier)
// ---------------------------------------------------------------------------

export const HOLDER_THRESHOLDS = [200, 400, 800, 1000] as const;
export const holderThresholdSchema = z.union([
  z.literal(200),
  z.literal(400),
  z.literal(800),
  z.literal(1000),
]);
export type HolderThreshold = z.infer<typeof holderThresholdSchema>;

export const HOLDER_WINDOWS = [1, 2, 3, 4] as const;
export const holderWindowSchema = z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4)]);
export type HolderWindow = z.infer<typeof holderWindowSchema>;

export const holderAccumulationRowSchema = z.object({
  symbol: z.string(),
  stock_name: z.string(),
  latest_close: z.number().nullable(),
  percent_of_custody_before: z.number(),
  percent_of_custody_after: z.number(),
  percent_of_custody_change: z.number(),
  shares_change: z.number(),
});
export type HolderAccumulationRow = z.infer<typeof holderAccumulationRowSchema>;

export const holderAccumulationDataSchema = z.object({
  market: z.string(),
  threshold: holderThresholdSchema,
  window: holderWindowSchema,
  as_of: z.string().nullable(),
  period_ago: z.string().nullable(),
  data_available: z.boolean(),
  rows: z.array(holderAccumulationRowSchema).default([]),
});
export type HolderAccumulationData = z.infer<typeof holderAccumulationDataSchema>;
export const holderAccumulationResponseSchema = z.union([
  holderAccumulationDataSchema,
  maskedTeaserSchema,
]);
export type HolderAccumulationResponse = z.infer<typeof holderAccumulationResponseSchema>;

// ---------------------------------------------------------------------------
// 13. GET /market/advance-decline — get_advance_decline (public breadth histogram)
// ---------------------------------------------------------------------------

export const advanceDeclineBucketsSchema = z.object({
  down_over_5pct: z.number(),
  down_3_to_5pct: z.number(),
  down_2_to_3pct: z.number(),
  down_1_to_2pct: z.number(),
  down_0_to_1pct: z.number(),
  unchanged: z.number(),
  up_0_to_1pct: z.number(),
  up_1_to_2pct: z.number(),
  up_2_to_3pct: z.number(),
  up_3_to_5pct: z.number(),
  up_over_5pct: z.number(),
});
export type AdvanceDeclineBuckets = z.infer<typeof advanceDeclineBucketsSchema>;

export const advanceDeclineResponseSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  data_available: z.boolean(),
  advancing_count: z.number(),
  declining_count: z.number(),
  unchanged_count: z.number(),
  buckets: advanceDeclineBucketsSchema,
});
export type AdvanceDeclineResponse = z.infer<typeof advanceDeclineResponseSchema>;

// ---------------------------------------------------------------------------
// 14. GET /market/index-contribution — get_index_contribution (Sponsor-tier,
//     APPROXIMATE market-cap-weight x price-change estimate — never TWSE's official
//     float-adjusted figure; see `contribution_caveat`).
// ---------------------------------------------------------------------------

export const indexContributionRowSchema = z.object({
  symbol: z.string(),
  stock_name: z.string(),
  weight_per: z.number(),
  pct_change: z.number(),
  contribution_estimate: z.number(),
});
export type IndexContributionRow = z.infer<typeof indexContributionRowSchema>;

export const indexContributionDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  contribution_caveat: z.string(),
  positive_contributors: z.array(indexContributionRowSchema).default([]),
  negative_contributors: z.array(indexContributionRowSchema).default([]),
});
export type IndexContributionData = z.infer<typeof indexContributionDataSchema>;
export const indexContributionResponseSchema = z.union([
  indexContributionDataSchema,
  maskedTeaserSchema,
]);
export type IndexContributionResponse = z.infer<typeof indexContributionResponseSchema>;

// ---------------------------------------------------------------------------
// 14b. GET /market/sector-heatmap — get_sector_heatmap (public, 產業別漲跌熱力圖)
// ---------------------------------------------------------------------------

export const sectorHeatmapRowSchema = z.object({
  industry: z.string(),
  avg_pct_change: z.number(),
  stock_count: z.number(),
  advancing_count: z.number(),
  declining_count: z.number(),
  total_turnover: z.number().nullable(),
  weighted_by_turnover: z.boolean(),
});
export type SectorHeatmapRow = z.infer<typeof sectorHeatmapRowSchema>;

export const sectorHeatmapDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  industries: z.array(sectorHeatmapRowSchema).default([]),
});
export type SectorHeatmapData = z.infer<typeof sectorHeatmapDataSchema>;
export const sectorHeatmapResponseSchema = sectorHeatmapDataSchema;
export type SectorHeatmapResponse = z.infer<typeof sectorHeatmapResponseSchema>;

// ---------------------------------------------------------------------------
// 15. GET /market/margin-short-balance — get_margin_short_total_market (Sponsor-tier)
// ---------------------------------------------------------------------------

export const marginShortTotalMarketRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  name: z.string(),
  buy: z.number(),
  sell: z.number(),
  today_balance: z.number(),
  // FinMind's own field name — trailing underscore avoids shadowing the JS reserved word.
  return_: z.number(),
});
export type MarginShortTotalMarketRow = z.infer<typeof marginShortTotalMarketRowSchema>;

export const marginShortBalanceDataSchema = rowsOutputSchema(marginShortTotalMarketRowSchema);
export const marginShortBalanceResponseSchema = z.union([
  marginShortBalanceDataSchema,
  maskedTeaserSchema,
]);
export type MarginShortBalanceResponse = z.infer<typeof marginShortBalanceResponseSchema>;

// ---------------------------------------------------------------------------
// 16. GET /market/etf-list — get_etf_list (public whole-market ETF list). Every field
//     mirrors `EtfListRow` (backend/src/tools/market_tools.py) field-for-field — a backend
//     rename becomes a compile error here, not a silent runtime mismatch.
// ---------------------------------------------------------------------------

export const ETF_DISTRIBUTION_FREQUENCIES = ["月配", "季配", "半年配", "年配", "不配息"] as const;
export const etfDistributionFrequencySchema = z.enum(ETF_DISTRIBUTION_FREQUENCIES).nullable();
export type EtfDistributionFrequency = z.infer<typeof etfDistributionFrequencySchema>;

export const etfListRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  name: z.string(),
  close: z.number().nullable(),
  change_abs: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  turnover: z.number().nullable(),
  return_1w_pct: z.number().nullable(),
  distribution_yield_ttm_pct: z.number().nullable(),
  distribution_frequency: etfDistributionFrequencySchema,
  aum: z.number().nullable(),
  expense_ratio_pct_approx: z.number().nullable(),
  symbol_type: symbolTypeSchema,
});
export type EtfListRow = z.infer<typeof etfListRowSchema>;

export const etfListDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  currentness_caveat: z.string(),
  aum_caveat: z.string(),
  expense_ratio_caveat: z.string(),
  distribution_caveat: z.string(),
  rows: z.array(etfListRowSchema).default([]),
});
export type EtfListData = z.infer<typeof etfListDataSchema>;
// Public — never Sponsor-masked, matching `priceMomentumResponseSchema`'s own un-unioned shape.
export const etfListResponseSchema = etfListDataSchema;
export type EtfListResponse = z.infer<typeof etfListResponseSchema>;

// ---------------------------------------------------------------------------
// 18. GET /market/dividend-calendar — get_dividend_calendar (public, ETF-only 股息日曆). Every
//     field mirrors `DividendCalendarEventRow` (backend/src/tools/market_tools.py) field-for-
//     field — a backend rename becomes a compile error here, not a silent runtime mismatch.
// ---------------------------------------------------------------------------

export const dividendCalendarEventRowSchema = z.object({
  symbol: z.string(),
  name: z.string(),
  ex_dividend_date: z.string(),
  last_buy_date: z.string(),
  payment_date: z.string().nullable(),
  cash_dividend: z.number(),
});
export type DividendCalendarEventRow = z.infer<typeof dividendCalendarEventRowSchema>;

export const dividendCalendarDataSchema = z.object({
  market: z.string(),
  year: z.number(),
  month: z.number(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  events: z.array(dividendCalendarEventRowSchema).default([]),
});
export type DividendCalendarData = z.infer<typeof dividendCalendarDataSchema>;
// Public — never Sponsor-masked, matching `etfListResponseSchema`'s own un-unioned shape.
export const dividendCalendarResponseSchema = dividendCalendarDataSchema;
export type DividendCalendarResponse = z.infer<typeof dividendCalendarResponseSchema>;

// ---------------------------------------------------------------------------
// 19. GET /market/material-announcement-feed — get_material_announcement_feed (public,
//     whole-market 重大訊息 feed). Mirrors `MaterialAnnouncementRow`/
//     `GetMaterialAnnouncementFeedOutput` (backend/src/tools/mops_tools.py) field-for-field.
//     Distinct from the existing per-symbol material-announcement shape (unused by any
//     frontend component today) — this is the whole-market list-view shape with a derived
//     `category` per row.
// ---------------------------------------------------------------------------

export const materialAnnouncementRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  announced_date: z.string(),
  announced_time: z.string(),
  subject: z.string(),
  detail: z.string(),
  qualifying_clause: z.string().nullable(),
  category: z.string(),
});
export type MaterialAnnouncementRow = z.infer<typeof materialAnnouncementRowSchema>;

export const materialAnnouncementFeedDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  category_counts: z.record(z.string(), z.number()).default({}),
  available_dates: z.array(z.string()).default([]),
  rows: z.array(materialAnnouncementRowSchema).default([]),
});
export type MaterialAnnouncementFeedData = z.infer<typeof materialAnnouncementFeedDataSchema>;
// Public — never Sponsor-masked, matching `dividendCalendarResponseSchema`'s own un-unioned shape.
export const materialAnnouncementFeedResponseSchema = materialAnnouncementFeedDataSchema;
export type MaterialAnnouncementFeedResponse = z.infer<
  typeof materialAnnouncementFeedResponseSchema
>;

// ---------------------------------------------------------------------------
// 20. GET /market/insider-transfer-declarations — get_insider_transfer_declarations (public,
//     whole-market 內部人股權轉讓事前申報 feed, t187ap12_L only). Mirrors
//     `InsiderTransferDeclarationRow`/`GetInsiderTransferDeclarationOutput`
//     (backend/src/tools/mops_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const insiderTransferDeclarationRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  filer_role: z.string(),
  person_name: z.string(),
  transfer_method: z.string(),
  transfer_shares: z.number(),
  max_daily_exchange_shares: z.number().nullable(),
  transferee: z.string().nullable(),
  current_shares_own: z.number(),
  current_shares_trust: z.number(),
  planned_shares_own: z.number(),
  planned_shares_trust: z.number(),
  projected_shares_own_after: z.number(),
  projected_shares_trust_after: z.number(),
  transfer_window_start: z.string(),
  transfer_window_end: z.string(),
});
export type InsiderTransferDeclarationRow = z.infer<typeof insiderTransferDeclarationRowSchema>;

export const insiderTransferDeclarationDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  available_dates: z.array(z.string()).default([]),
  rows: z.array(insiderTransferDeclarationRowSchema).default([]),
});
export type InsiderTransferDeclarationData = z.infer<typeof insiderTransferDeclarationDataSchema>;
// Public — never Sponsor-masked, matching `dividendCalendarResponseSchema`'s own un-unioned shape.
export const insiderTransferDeclarationResponseSchema = insiderTransferDeclarationDataSchema;
export type InsiderTransferDeclarationResponse = z.infer<
  typeof insiderTransferDeclarationResponseSchema
>;

// Frontend-invented UI-only shape (no backend citation needed, web/CLAUDE.md §2) — the
// sortable columns `EtfListTable` lets a caller sort by, and the current sort direction.
export const ETF_SORT_KEYS = [
  "name",
  "close",
  "change_pct",
  "turnover",
  "return_1w_pct",
  "distribution_yield_ttm_pct",
  "aum",
  "expense_ratio_pct_approx",
] as const;
export type EtfSortKey = (typeof ETF_SORT_KEYS)[number];
export type SortDirection = "asc" | "desc";

// ---------------------------------------------------------------------------
// 17. GET /market/daily-signal — get_daily_signal (Sponsor-tier). Mirrors
//     `DailySignalRow`/`DailySignalHighlight`/`GetDailySignalOutput`
//     (backend/src/tools/market_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const HOLDING_STATUSES = ["新增", "刪除", "加碼", "減碼", "no_prior_data"] as const;
export const holdingStatusSchema = z.enum(HOLDING_STATUSES);
export type HoldingStatus = z.infer<typeof holdingStatusSchema>;

export const dailySignalRowSchema = z.object({
  component_stock_id: z.string(),
  component_stock_name: z.string(),
  close: z.number().nullable(),
  change_pct: z.number().nullable(),
  status: holdingStatusSchema,
  shares_delta: z.number(),
  pct_change: z.number().nullable(),
  buying_etf_count: z.number(),
  selling_etf_count: z.number(),
});
export type DailySignalRow = z.infer<typeof dailySignalRowSchema>;

export const dailySignalHighlightSchema = z.object({
  component_stock_id: z.string(),
  component_stock_name: z.string(),
  held_market_value_delta: z.number().nullable(),
  buying_etf_count: z.number(),
  selling_etf_count: z.number(),
});
export type DailySignalHighlight = z.infer<typeof dailySignalHighlightSchema>;

export const dailySignalDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  prior_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  total_stock_count: z.number().default(0),
  new_count: z.number().default(0),
  removed_count: z.number().default(0),
  increased_count: z.number().default(0),
  decreased_count: z.number().default(0),
  top_inflow: dailySignalHighlightSchema.nullable(),
  top_outflow: dailySignalHighlightSchema.nullable(),
  rows: z.array(dailySignalRowSchema).default([]),
});
export const dailySignalResponseSchema = z.union([dailySignalDataSchema, maskedTeaserSchema]);
export type DailySignalResponse = z.infer<typeof dailySignalResponseSchema>;

// ---------------------------------------------------------------------------
// 21. GET /market/index-strip — get_market_index_strip (public). Mirrors
//     `MarketIndexStripItemRow`/`GetMarketIndexStripOutput`
//     (backend/src/tools/market_index_strip_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const marketIndexStripItemSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.number(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  as_of: z.string(),
});
export type MarketIndexStripItem = z.infer<typeof marketIndexStripItemSchema>;

export const marketIndexStripDataSchema = z.object({
  rows: z.array(marketIndexStripItemSchema).default([]),
});
// Public — never Sponsor-masked, same un-unioned convention as `dividendCalendarResponseSchema`.
export const marketIndexStripResponseSchema = marketIndexStripDataSchema;
export type MarketIndexStripResponse = z.infer<typeof marketIndexStripResponseSchema>;

// ---------------------------------------------------------------------------
// 22. GET /market/tick-snapshot — get_tick_snapshot (Sponsor-tier). Mirrors
//     `TickSnapshotRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const tickSnapshotRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  amount: z.number(),
  average_price: z.number(),
  buy_price: z.number(),
  buy_volume: z.number(),
  change_price: z.number(),
  change_rate: z.number(),
  close: z.number(),
  high: z.number(),
  low: z.number(),
  open: z.number(),
  sell_price: z.number(),
  sell_volume: z.number(),
  total_amount: z.number(),
  total_volume: z.number(),
  volume: z.number(),
  volume_ratio: z.number(),
  yesterday_volume: z.number(),
  tick_type: z.string(),
});
export type TickSnapshotRow = z.infer<typeof tickSnapshotRowSchema>;

export const tickSnapshotDataSchema = rowsOutputSchema(tickSnapshotRowSchema);
export const tickSnapshotResponseSchema = z.union([tickSnapshotDataSchema, maskedTeaserSchema]);
export type TickSnapshotResponse = z.infer<typeof tickSnapshotResponseSchema>;

// ---------------------------------------------------------------------------
// 23. GET /market/futures-snapshot — get_futures_snapshot (Sponsor-tier). Mirrors
//     `FuturesSnapshotRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const futuresSnapshotRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  change_price: z.number(),
  change_rate: z.number(),
  average_price: z.number(),
  volume: z.number(),
  total_volume: z.number(),
  amount: z.number(),
  total_amount: z.number(),
  yesterday_volume: z.number(),
  buy_price: z.number(),
  buy_volume: z.number(),
  sell_price: z.number(),
  sell_volume: z.number(),
  volume_ratio: z.number(),
  tick_type: z.string(),
});
export type FuturesSnapshotRow = z.infer<typeof futuresSnapshotRowSchema>;

export const futuresSnapshotDataSchema = rowsOutputSchema(futuresSnapshotRowSchema);
export const futuresSnapshotResponseSchema = z.union([
  futuresSnapshotDataSchema,
  maskedTeaserSchema,
]);
export type FuturesSnapshotResponse = z.infer<typeof futuresSnapshotResponseSchema>;

// ---------------------------------------------------------------------------
// 24. GET /derivatives/taiex-vix — get_taiex_vix (Sponsor-tier). Mirrors
//     `OptionVixRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const optionVixRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  time: z.string(),
  vix: z.number(),
});
export type OptionVixRow = z.infer<typeof optionVixRowSchema>;

export const optionVixDataSchema = rowsOutputSchema(optionVixRowSchema);
export const optionVixResponseSchema = z.union([optionVixDataSchema, maskedTeaserSchema]);
export type OptionVixResponse = z.infer<typeof optionVixResponseSchema>;

// ---------------------------------------------------------------------------
// 25. GET /market/disposition-board — get_disposition_board (Sponsor-tier). Mirrors
//     `DispositionBoardCardRow`/`GetDispositionBoardOutput`
//     (backend/src/tools/disposition_board_tools.py, backend/src/schemas/capabilities.py)
//     field-for-field. 3 buckets this pass (at_risk/in_disposition/exiting_soon) — 自結發布 is
//     out of scope (see the backend plan this mirrors).
// ---------------------------------------------------------------------------

export const DISPOSITION_BUCKETS = ["at_risk", "in_disposition", "exiting_soon"] as const;
export const dispositionBucketSchema = z.enum(DISPOSITION_BUCKETS);
export type DispositionBucket = z.infer<typeof dispositionBucketSchema>;

export const dispositionBoardCardRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  security_type: z.string(),
  close: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  turnover: z.number().nullable(),
  institutional_net: z.number().nullable(),
  margin_ratio: z.number().nullable(),
  margin_caveat: z.string().nullable(),
  margin_health_status: healthStatusSchema.nullable(),
  day_trading_ratio: z.number().nullable(),
  bucket: dispositionBucketSchema,
  disposition_measure: z.string().nullable(),
  disposition_period_start: z.string().nullable(),
  disposition_period_end: z.string().nullable(),
  matching_interval_minutes: z.number().nullable(),
  flags: z.array(z.string()).default([]),
  attention_flags_remaining: z.number().nullable(),
  estimated_days_until_disposition: z.number().nullable(),
  days_until_exiting: z.number().nullable(),
  projection_caveat: z.string(),
});
export type DispositionBoardCardRow = z.infer<typeof dispositionBoardCardRowSchema>;

export const dispositionBoardDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  bucket_counts: z.record(z.string(), z.number()).default({}),
  cards: z.array(dispositionBoardCardRowSchema).default([]),
});
export type DispositionBoardData = z.infer<typeof dispositionBoardDataSchema>;
export const dispositionBoardResponseSchema = z.union([
  dispositionBoardDataSchema,
  maskedTeaserSchema,
]);
export type DispositionBoardResponse = z.infer<typeof dispositionBoardResponseSchema>;
