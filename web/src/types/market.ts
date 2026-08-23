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
  held_market_value: z.number().nullable().optional(),
  net_shares_flow: z.number(),
  buying_etf_count: z.number(),
  selling_etf_count: z.number(),
});
export type EtfMoneyFlowRow = z.infer<typeof etfMoneyFlowRowSchema>;

export const etfMoneyFlowDataSchema = z.object({
  market: z.string(),
  start_date: z.string(),
  end_date: z.string(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
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
