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
 * A key absent from either map falls back to a humanized form of the identifier (`fieldLabel`/
 * `cardTypeLabel` below) rather than throwing — an unmapped field/type is a labeling gap to
 * notice and fill in, never a crash, and never a card the user can't see at all.
 */
import type { useTranslations } from "next-intl";

type Translator = ReturnType<typeof useTranslations>;

/** Keys with a mapped label in the `chat.cards.fields` message namespace — kept as a literal
 * list (not derived from the namespace) so `fieldLabel` can check membership without a runtime
 * i18n lookup throwing on a miss. */
export const LABELED_FIELDS = new Set([
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
  // institutional_flow / InstitutionalFlowRow — InstitutionalFlowTimeseries chart legend + totals table
  "buy",
  "sell",
  "net",
  "investor",
  "investor_totals",
  // institutional_flow's rolling_net — 籌碼分析's 摘要 card ChartWithDataSheet expanded rows
  // (InstitutionalFlowRollingNet)
  "net_5d",
  "net_10d",
  "net_20d",
  // institutional_flow_wide / institutional_flow_total_market — DivergingBarChart category axis
  "foreign_investor",
  "investment_trust",
  "dealer",
  "dealer_self",
  "dealer_hedging",
  "foreign_dealer_self",
  "governmentbank",
  // margin_short — GroupedBarChart legend
  "margin_purchase_today_balance",
  "short_sale_today_balance",
  // put_call_ratio — GroupedBarChart legend
  "call_volume",
  "put_volume",
  // block_trade / block_trade_broker_report / chip_broker_agg — LeaderboardTable columns
  "symbol",
  "market",
  "broker_id",
  "broker_name",
  "price",
  "buy_volume",
  "sell_volume",
  // chip_flows — 籌碼分析's 分點 買賣超 Top15 tables' ChartWithDataSheet expanded rows
  // (aggregated per-broker over the fetched window, computed client-side in
  // chip-analysis-panel.tsx, not a raw backend field — still routed through this shared map so
  // its data-sheet column header isn't a raw snake_case key)
  "turnover_share",
  "buy_price",
  "sell_price",
  "trade_type",
  "trading_money",
  // securities_lending — TimeseriesLineChart legend
  "fee_rate",
  "original_return_date",
  "original_lending_period",
  // foreign_shareholding — TimeseriesLineChart legend
  "foreign_investment_remaining_shares",
  "foreign_investment_shares",
  "foreign_investment_remain_ratio",
  "foreign_investment_shares_ratio",
  "foreign_investment_upper_limit_ratio",
  "chinese_investment_upper_limit_ratio",
  // daily_short_sale_balances — TimeseriesLineChart legend
  "margin_short_sales_previous_day_balance",
  "margin_short_sales_short_sales",
  "margin_short_sales_short_covering",
  "margin_short_sales_stock_redemption",
  "margin_short_sales_current_day_balance",
  "margin_short_sales_quota",
  // margin_short_total_market — TimeseriesLineChart legend
  "today_balance",
  "yesterday_balance",
  "return_",
  // government_bank_flows — TimeseriesLineChart legend
  "buy_amount",
  "sell_amount",
  // margin_maintenance — TimeseriesLineChart legend
  "total_exchange_margin_maintenance",
  // cb_flow — TimeseriesLineChart legend
  "change",
  "number_of_transactions",
  "trading_value",
  "avg_price",
  "next_ref_price",
  "next_max_limit",
  "next_min_limit",
  "foreign_investor_buy",
  "foreign_investor_sell",
  "foreign_investor_overbuy",
  "investment_trust_buy",
  "investment_trust_sell",
  "investment_trust_overbuy",
  "dealer_self_buy",
  "dealer_self_sell",
  "dealer_self_overbuy",
  "total_overbuy",
  "custody_balance",
  "last_month_balance",
  "change_percent",
  "issued_units",
  "custody_accounts",
  "pledged_units",
  // futures_options_daily — TimeseriesLineChart legend
  "spread",
  "spread_per",
  "settlement_price",
  "open_interest",
  "strike_price",
  // taiex_vix — TimeseriesLineChart legend
  "vix",
  // futures_spread_trading — TimeseriesLineChart legend
  "best_bid",
  "best_ask",
  "last_price",
  "observed_at",
  "historical_max",
  "historical_min",
  "spread_to_spread_volume",
  "spread_to_single_volume",
  // asset_swap_daily — TimeseriesLineChart legend
  "notional_amount",
  "rate_lowest",
  "rate_highest",
  "rate_average",
  "premium_lowest",
  "premium_highest",
  "premium_average",
  "contract_term_years",
  // dividend_result — TimeseriesLineChart legend
  "before_price",
  "after_price",
  "stock_and_cache_dividend",
  "stock_or_cache_dividend",
  "max_price",
  "min_price",
  "open_price",
  "reference_price",
  // month_revenue — TimeseriesLineChart legend
  "revenue",
  // international_stock — TimeseriesLineChart legend
  "adj_close",
  // macro_indicators — TimeseriesLineChart legend
  "cash_buy",
  "cash_sell",
  "spot_buy",
  "spot_sell",
  "interest_rate",
  "value",
  "fear_greed",
  // business_indicator — TimeseriesLineChart legend
  "leading",
  "leading_notrend",
  "coincident",
  "coincident_notrend",
  "lagging",
  "lagging_notrend",
  "monitoring",
  // price_tick — TimeseriesLineChart legend
  "deal_price",
  // order_book_stats — TimeseriesLineChart legend
  "total_buy_order",
  "total_buy_volume",
  "total_sell_order",
  "total_sell_volume",
  "total_deal_order",
  "total_deal_volume",
  "total_deal_money",
  // taiex_index_snapshot — TimeseriesLineChart legend
  "taiex",
  // day_trading — TimeseriesLineChart legend (buy_amount/sell_amount already declared above)
  // price_multi_tf — TimeseriesLineChart legend
  "trading_volume",
  "trading_turnover",
  // etf_money_flow — LeaderboardTable
  "net_shares_flow",
  "buying_etf_count",
  "selling_etf_count",
  // active_etf_report — DataSheet/SnapshotCard stat grid
  "report_date",
  "prior_date",
  "fund_size",
  "fund_size_prior",
  "fund_size_change_pct",
  // dividend_schedule — EventTimeline summary
  "announced_date",
  "cash_dividend",
  "stock_dividend",
  "cash_ex_dividend_date",
  "stock_ex_dividend_date",
  // insider_activity — EventTimeline summary
  "position",
  "transaction",
  "shares",
  "start_date",
  "ownership",
  // price_limit — TimeseriesLineChart legend
  "limit_up",
  "limit_down",
  // insider_shareholding — DataSheet FlatSheet columns (DirectorShareholding)
  "person_name",
  "title",
  "shares_at_election",
  "current_shares",
  "pledged_shares",
  "pledged_ratio",
  // active_etf_report — DataSheet FlatSheet columns (ActiveEtfReportRow)
  "component_stock_id",
  "component_stock_name",
  "status",
  "shares_delta",
  "pct_change",
  "current_weight",
  "weight_delta_pct",
  // cb_profile — DataSheet FlatSheet columns (ConvertibleBondInfoRow /
  // ConvertibleBondDailyOverviewRow / ConvertibleBondPutProvisionRow)
  "name",
  "initial_date_of_conversion",
  "due_date_of_conversion",
  "issuance_amount",
  "initial_date_of_stop_conversion",
  "due_date_of_stop_conversion",
  "conversion_price",
  "next_effective_date_of_conversion_price",
  "latest_initial_date_of_put",
  "latest_due_date_of_put",
  "latest_put_price",
  "initial_date_of_early_redemption",
  "due_date_of_early_redemption",
  "early_redemption_price",
  "date_of_delisted",
  "outstanding_amount",
  "reference_price",
  "price_of_underlying_stock",
  "initial_date_of_suspension",
  "due_date_of_suspension",
  "coupon_rate",
  "date",
  "put_price",
  "put_yield_rate",
  // loan_collateral_balance — DataSheet FlatSheet columns (LoanCollateralBalanceRow)
  "exchange_market",
  "margin_previous_day_balance",
  "margin_buy",
  "margin_sell",
  "margin_cash_redemption",
  "margin_current_day_balance",
  "margin_next_day_quota",
  "securities_firm_loan_previous_day_balance",
  "securities_firm_loan_buy",
  "securities_firm_loan_sell",
  "securities_firm_loan_cash_redemption",
  "securities_firm_loan_replacement",
  "securities_firm_loan_current_day_balance",
  "securities_firm_loan_next_day_quota",
  "unrestricted_loan_previous_day_balance",
  "unrestricted_loan_buy",
  "unrestricted_loan_sell",
  "unrestricted_loan_cash_redemption",
  "unrestricted_loan_replacement",
  "unrestricted_loan_current_day_balance",
  "unrestricted_loan_next_day_quota",
  "securities_finance_secured_loan_previous_day_balance",
  "securities_finance_secured_loan_buy",
  "securities_finance_secured_loan_sell",
  "securities_finance_secured_loan_cash_redemption",
  "securities_finance_secured_loan_replacement",
  "securities_finance_secured_loan_current_day_balance",
  "securities_finance_secured_loan_next_day_quota",
  "settlement_margin_previous_day_balance",
  "settlement_margin_buy",
  "settlement_margin_sell",
  "settlement_margin_cash_redemption",
  "settlement_margin_replacement",
  "settlement_margin_current_day_balance",
  "settlement_margin_next_day_quota",
  // theme_members / symbol_themes / theme_tiers — DataSheet FlatSheet columns
  // (ThemeMember / SymbolTheme / ThemeTier), plus the not-found fallback fields
  // (GetThemeMembersOutput/GetThemeTiersOutput's own `found`/`message`/`name_en`/`member_count`)
  "confidence",
  "sub_industry",
  // analysis-report chart sheets (series points, seasonality, trailing returns, premium/discount)
  "year",
  "metric",
  "period",
  "label",
  "index",
  "deviation_pct",
  "horizon",
  "return_pct",
  "premium_discount_pct",
  "weight",
  "close_price",
  "estimated_nav",
  "prior_day_nav",
  "units_outstanding_change",
  "theme_key",
  "name_zh",
  "name_en",
  "kind",
  "tier",
  "updated_at",
  "found",
  "message",
  "member_count",
  // financial_statement — DataSheet FlatSheet columns (FinancialStatementLine)
  "statement_type",
  "origin_name",
  // month_revenue — ChartWithDataSheet expanded rows (MonthRevenueRow)
  "country",
  "revenue_month",
  "revenue_year",
  "create_time",
  // etf_overlap — CompareTable pairwise rows (PairOverlapRow)
  "pair",
  "overlap_pct",
  "shared_holding_count",
  // institutional_holders — LeaderboardTable rows (InstitutionalHolderRow); `pct_change` is
  // already labeled above (shared with `component_stock`'s own field of the same name)
  "pct_held",
  // sector_overview / industry_overview — LeaderboardTable rows
  // (SectorOverviewCompanyRow / IndustryOverviewCompanyRow)
  "market_weight",
  "rating",
  "row_kind",
  "sector_key",
  "industry_key",
  // analyst_consensus — SnapshotCard stat grid (AnalystConsensus)
  "mean_target",
  "median_target",
  "high_target",
  "low_target",
  "number_of_analysts",
  "recommendation_key",
  "strong_buy",
  "hold",
  "strong_sell",
  "latest_upgrade_downgrade_firm",
  "latest_upgrade_downgrade_action",
  "forward_eps_estimate",
  "forward_revenue_estimate",
  "long_term_growth_estimate",
  "current_year_growth_estimate",
  "next_year_growth_estimate",
  "eps_trend_current",
  "eps_trend_7_days_ago",
  "eps_trend_30_days_ago",
  "eps_trend_60_days_ago",
  "eps_trend_90_days_ago",
  "eps_revisions_up_last_30_days",
  "eps_revisions_down_last_30_days",
  // esg_score — SnapshotCard stat grid (EsgScore)
  "total_esg_score",
  "esg_risk_percentile",
  // options_chain — LeaderboardTable rows (OptionContractRow); `last_price`/`open_interest`
  // are already labeled above (shared with futures/margin data of the same field name)
  "expiry",
  "contract_symbol",
  "side",
  "strike",
  // fund_comparison — CompareTable entity rows (FundComparisonRow)
  "window_start",
  "window_end",
  "total_return_pct",
  "price_unavailable_reason",
  "unavailable_note",
  // get_etf_money_flow's empty-result fallback (GetEtfMoneyFlowOutput)
  "end_date",
  "data_available",
  "unavailable_reason",
  // get_symbol_directory_search (the roster the lookup read)
  "roster_date",
]);

/** Raw enum values a card cell can carry, per column, with a translated label in
 * `chat.cards.fieldValues.<column>.<value>` — e.g. a theme's `kind` ("industry"/"concept") and a
 * supply-chain `tier`. A value not listed falls through to itself (free text such as a company name
 * must never be run through a lookup). */
const LABELED_VALUES: Record<string, ReadonlySet<string>> = {
  kind: new Set(["industry", "concept"]),
  tier: new Set(["upstream", "midstream", "downstream"]),
  recommendation_key: new Set(["strong_buy", "buy", "hold", "underperform", "sell", "none"]),
  latest_upgrade_downgrade_action: new Set(["main", "up", "down", "init", "reit"]),
  sector: new Set([
    "technology",
    "financial_services",
    "communication_services",
    "consumer_cyclical",
    "consumer_defensive",
    "healthcare",
    "industrials",
    "energy",
    "utilities",
    "basic_materials",
    "realestate",
    "real_estate",
  ]),
};

export function valueLabel(t: Translator, column: string, value: string): string {
  return LABELED_VALUES[column]?.has(value) ? t(`fieldValues.${column}.${value}`) : value;
}

/** Resolves `key` to its translated label via the `chat.cards.fields` namespace, falling back to
 * the raw key for anything not yet mapped (see module docstring). */
export function fieldLabel(t: Translator, key: string): string {
  return LABELED_FIELDS.has(key) ? t(`fields.${key}`) : humanizeKey(key);
}

/** Last-resort label for an unmapped key: `eps_trend_60_days_ago` → "Eps trend 60 days ago" — a
 * readable phrase instead of a code identifier, until the key gets a real translation. */
function humanizeKey(key: string): string {
  const words = key.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
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
  "analyst_consensus",
  "morning_brief",
  "esg_score",
  "symbol_search",
]);

/** Resolves `cardType` to its translated label via the `chat.cards.types` namespace, falling
 * back to the raw `card_type` string for anything not yet mapped (see module docstring). */
export function cardTypeLabel(t: Translator, cardType: string): string {
  return LABELED_CARD_TYPES.has(cardType) ? t(`types.${cardType}`) : humanizeKey(cardType);
}
