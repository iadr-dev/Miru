/**
 * Human-readable labels for the raw snake_case identifiers `SnapshotCard`/`FlatCard` would
 * otherwise render verbatim: field keys (across every "snapshot"-shaped card type —
 * `company_profile`, `price_quote`, `stock_score`, `etf_fund_info`) and the `card_type` string
 * itself (shown as a corner label on every card, and as the flat-fallback's own caption). Mirrors
 * the field names on `backend/src/tools/market_tools.py::GetCompanyProfileOutput`,
 * `score_tools.py::ScoreResult`, `etf_pcf_tools.py::EtfFundInfo`, `schemas/capabilities.py::
 * EODBar`, and every `card_type` literal in `card-registry.ts`'s `CARD_TYPE_SHAPE` map — a
 * backend field/card-type rename needs its label updated here too (no generated linkage, so this
 * list drifts only on an explicit backend rename).
 *
 * A key absent from either map falls back to the raw identifier itself (`fieldLabel`/
 * `cardTypeLabel` below) rather than throwing — an unmapped field/type is a labeling gap to
 * notice and fill in, never a crash, and never a card the user can't see at all.
 */
import type { useTranslations } from "next-intl";

type Translator = ReturnType<typeof useTranslations>;

/** Keys with a mapped label in the `chat.cards.fields` message namespace — kept as a literal
 * list (not derived from the namespace) so `fieldLabel` can check membership without a runtime
 * i18n lookup throwing on a miss. */
const LABELED_FIELDS = new Set([
  // company_profile
  "sector",
  "industry",
  "market_value",
  "per",
  "pbr",
  "dividend_yield",
  "eps",
  "chairman",
  "auditor",
  "headquarters",
  "website",
  "capital",
  "spokesperson",
  "founding_date",
  "industry_classification_code",
  "revenue_yoy",
  "margins",
  "sector_caveat",
  "dividend_caveat",
  // price_quote / EODBar
  "trade_date",
  "open",
  "high",
  "low",
  "close",
  "volume",
  "turnover",
  // stock_score / ScoreResult
  "as_of",
  "composite",
  "band",
  // etf_fund_info / EtfFundInfo
  "fund_name",
  "fund_type",
  "tracked_index",
  "units_outstanding",
  "listed_date",
  "manager_company",
  // institutional_flow / InstitutionalFlowRow — timeseries chart legend
  "buy",
  "sell",
  // block_trade / block_trade_broker_report / chip_broker_agg — LeaderboardTable columns
  "symbol",
  "market",
  "broker_id",
  "broker_name",
  "price",
  "buy_volume",
  "sell_volume",
  "buy_price",
  "sell_price",
  "trade_type",
  "trading_money",
]);

/** Resolves `key` to its translated label via the `chat.cards.fields` namespace, falling back to
 * the raw key for anything not yet mapped (see module docstring). */
export function fieldLabel(t: Translator, key: string): string {
  return LABELED_FIELDS.has(key) ? t(`fields.${key}`) : key;
}

/** Every `card_type` literal `card-registry.ts`'s `CARD_TYPE_SHAPE` maps — kept as its own set
 * (not derived from that map) so this module has no import-time dependency on the registry. */
const LABELED_CARD_TYPES = new Set([
  "margin_short",
  "margin_short_total_market",
  "institutional_flow_wide",
  "foreign_shareholding",
  "securities_lending",
  "daily_short_sale_balances",
  "government_bank_flows",
  "margin_maintenance",
  "cb_flow",
  "futures_options_daily",
  "taiex_vix",
  "dealer_volume",
  "futures_spread_trading",
  "asset_swap_daily",
  "dividend_result",
  "month_revenue",
  "market_value",
  "international_stock",
  "us_price_minute",
  "macro_indicators",
  "business_indicator",
  "institutional_flow",
  "price_tick",
  "valuation_ratios",
  "order_book_stats",
  "taiex_index_snapshot",
  "day_trading",
  "total_return_index",
  "ten_year_price",
  "intraday_kbar",
  "price_multi_tf",
  "index_snapshots",
  "price_limit",
  "etf_fund_info",
  "live_quote_snapshot",
  "price_quote",
  "company_profile",
  "stock_score",
  "financial_statement",
  "loan_collateral_balance",
  "active_etf_report",
  "cb_profile",
  "insider_shareholding",
  "theme_members",
  "symbol_themes",
  "theme_tiers",
  "put_call_ratio",
  "equity_screener",
  "tag_screener",
  "score_leaderboard",
  "etf_money_flow",
  "market_value_weight",
  "institutional_holders",
  "sector_overview",
  "industry_overview",
  "chip_broker_agg",
  "chip_flows",
  "etf_overlap",
  "fund_comparison",
  "fund_holdings",
  "shareholder_dispersion",
  "industry_money_flow",
  "derivatives_institutional",
  "institutional_flow_total_market",
  "insider_activity",
  "block_trade_broker_report",
  "block_trade",
  "dividend_schedule",
]);

/** Resolves `cardType` to its translated label via the `chat.cards.types` namespace, falling
 * back to the raw `card_type` string for anything not yet mapped (see module docstring). */
export function cardTypeLabel(t: Translator, cardType: string): string {
  return LABELED_CARD_TYPES.has(cardType) ? t(`types.${cardType}`) : cardType;
}
