/**
 * Maps every real `card_type` the backend emits to one of 7 visual shape buckets, so the ~70
 * distinct tool output shapes render through 7 real components instead of one flat fallback.
 * Mirrors the `card_type` literals set across `backend/src/tools/*.py` — a card type absent
 * from this map is rendered by `RichCardView`'s existing key-value fallback, never a crash.
 *
 * The bucket a card type lands in follows its actual field shape, not its literal name — a few
 * backend names ("taiex_index_snapshot", "index_snapshots") say "snapshot" but are actually
 * intraday time series (rows keyed by trade_date/time), so they're bucketed as `timeseries`.
 */

export const CARD_SHAPES = [
  "timeseries",
  "snapshot",
  "data_sheet",
  "leaderboard",
  "comparison",
  "distribution",
  "event_list",
] as const;
export type CardShape = (typeof CARD_SHAPES)[number];

export const CARD_TYPE_SHAPE: Record<string, CardShape> = {
  // timeseries — rows keyed by date/time, one or more numeric series
  margin_short: "timeseries",
  margin_short_total_market: "timeseries",
  institutional_flow_wide: "timeseries",
  foreign_shareholding: "timeseries",
  securities_lending: "timeseries",
  daily_short_sale_balances: "timeseries",
  government_bank_flows: "timeseries",
  margin_maintenance: "timeseries",
  cb_flow: "timeseries",
  futures_options_daily: "timeseries",
  taiex_vix: "timeseries",
  dealer_volume: "timeseries",
  futures_spread_trading: "timeseries",
  asset_swap_daily: "timeseries",
  dividend_result: "timeseries",
  month_revenue: "timeseries",
  market_value: "timeseries",
  international_stock: "timeseries",
  us_price_minute: "timeseries",
  macro_indicators: "timeseries",
  business_indicator: "timeseries",
  institutional_flow: "timeseries",
  price_tick: "timeseries",
  valuation_ratios: "timeseries",
  order_book_stats: "timeseries",
  taiex_index_snapshot: "timeseries",
  day_trading: "timeseries",
  total_return_index: "timeseries",
  ten_year_price: "timeseries",
  intraday_kbar: "timeseries",
  price_multi_tf: "timeseries",
  index_snapshots: "timeseries",
  price_limit: "timeseries",

  // snapshot — one flat entity at one point in time
  etf_fund_info: "snapshot",
  live_quote_snapshot: "snapshot",
  price_quote: "snapshot",
  company_profile: "snapshot",
  stock_score: "snapshot",

  // data_sheet — line-items/rows with a category dimension, often multi-period
  financial_statement: "data_sheet",
  loan_collateral_balance: "data_sheet",
  active_etf_report: "data_sheet",
  cb_profile: "data_sheet",
  insider_shareholding: "data_sheet",
  theme_members: "data_sheet",
  symbol_themes: "data_sheet",
  theme_tiers: "data_sheet",
  put_call_ratio: "data_sheet",

  // leaderboard — many distinct symbols/entities ranked or filtered
  equity_screener: "leaderboard",
  tag_screener: "leaderboard",
  score_leaderboard: "leaderboard",
  etf_money_flow: "leaderboard",
  market_value_weight: "leaderboard",
  institutional_holders: "leaderboard",
  sector_overview: "leaderboard",
  industry_overview: "leaderboard",
  chip_broker_agg: "leaderboard",
  chip_flows: "leaderboard",

  // comparison — 2+ named entities compared on the same fields
  etf_overlap: "comparison",
  fund_comparison: "comparison",

  // distribution — a total broken into named buckets/categories at one point in time
  fund_holdings: "distribution",
  shareholder_dispersion: "distribution",
  industry_money_flow: "distribution",
  derivatives_institutional: "distribution",
  institutional_flow_total_market: "distribution",

  // event_list — discrete dated occurrences, not a continuous series
  insider_activity: "event_list",
  block_trade_broker_report: "event_list",
  block_trade: "event_list",
  dividend_schedule: "event_list",
};

export function shapeForCardType(cardType: string): CardShape | null {
  return CARD_TYPE_SHAPE[cardType] ?? null;
}
