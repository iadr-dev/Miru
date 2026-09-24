from __future__ import annotations

from enum import StrEnum


class TimingClass(StrEnum):
    """How current a SourceResult's data can be. Every result is tagged."""

    LIVE_ISH = "live-ish"  # bounded snapshot, NOT streaming
    OFFICIAL_EOD = "official-eod"
    EVENT_DRIVEN = "event-driven"
    REFERENCE = "reference"  # news/social/web/static — near-real-time or slow-moving


class DataClass(StrEnum):
    """The single access gate — vendor-neutral licensed/paid vs free."""

    PUBLIC = "public"
    SPONSOR = "sponsor"


class TLSPolicy(StrEnum):
    """M6D TLS policy. No code path may downgrade silently."""

    STRICT = "strict"
    COMPATIBILITY = "compatibility"
    UNSAFE_EXPLICIT = "unsafe-explicit"


class Market(StrEnum):
    """Drives routing, calendar rules, currency, timezone. Add by config."""

    TW = "TW"
    US = "US"
    GB = "GB"
    JP = "JP"
    HK = "HK"
    EU = "EU"
    # No single-country home (global macro benchmarks, world commodity prices) — see
    # sources/finmind/global_economic.py for the FinMind datasets modeled under this market.
    GLOBAL = "GLOBAL"


class Tier(StrEnum):
    """User subscription tier — gates paid data + throughput only, never a recommendation
    (governance.md §9)."""

    FREE = "free"
    PREMIUM = "premium"
    PRO = "pro"


class Capability(StrEnum):
    """What callers request — never a source id."""

    # --- Price / market data (core equity) ---
    PRICE_EOD = "price_eod"
    PRICE_EOD_ADJUSTED = "price_eod_adjusted"
    PRICE_INTRADAY = "price_intraday"
    PRICE_TICK = "price_tick"
    PRICE_WEEKLY = "price_weekly"
    PRICE_MONTHLY = "price_monthly"
    PRICE_LIMIT = "price_limit"
    INTRADAY_KBAR = "intraday_kbar"
    TICK_SNAPSHOT = "tick_snapshot"
    ORDER_BOOK_STATS = "order_book_stats"
    TEN_YEAR_PRICE = "ten_year_price"
    TOTAL_RETURN_INDEX = "total_return_index"

    # --- Corporate fundamentals / financials ---
    FINANCIALS = "financials"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW_STATEMENT = "cash_flow_statement"
    MONTH_REVENUE = "month_revenue"
    VALUATION_RATIOS = "valuation_ratios"
    BUSINESS_INDICATOR = "business_indicator"
    DIVIDENDS = "dividends"
    DIVIDEND_RESULT = "dividend_result"
    CORPORATE_ACTIONS = "corporate_actions"
    CAPITAL_REDUCTION_REFERENCE_PRICE = "capital_reduction_reference_price"
    PAR_VALUE_CHANGE = "par_value_change"
    SPLIT_PRICE = "split_price"
    STOCK_DELISTING = "stock_delisting"
    TRADING_SUSPENSION = "trading_suspension"
    DAY_TRADING_SUSPENSION = "day_trading_suspension"
    INSIDER_ACTIVITY = "insider_activity"
    # TW MOPS 內部人股權轉讓事前申報 (t187ap12_L, advance-transfer-plan declaration only) — distinct
    # from INSIDER_ACTIVITY, which is yfinance/US Form-4-derived and never serves Market.TW.
    INSIDER_TRANSFER_DECLARATION = "insider_transfer_declaration"

    # --- Institutional flow / chip data ---
    INSTITUTIONAL_FLOW = "institutional_flow"
    INSTITUTIONAL_FLOW_WIDE = "institutional_flow_wide"
    INSTITUTIONAL_FLOW_TOTAL_MARKET = "institutional_flow_total_market"
    INSTITUTIONAL_HOLDERS = "institutional_holders"
    CHIP_BROKER = "chip_broker"
    CHIP_BROKER_AGG = "chip_broker_agg"
    WARRANT_CHIP_BROKER = "warrant_chip_broker"
    BLOCK_TRADE = "block_trade"
    BLOCK_TRADE_BROKER_REPORT = "block_trade_broker_report"
    DAY_TRADING = "day_trading"
    GOVERNMENT_BANK_BUYSELL = "government_bank_buysell"

    # --- Margin / short / lending ---
    MARGIN_SHORT = "margin_short"
    MARGIN_SHORT_TOTAL_MARKET = "margin_short_total_market"
    MARGIN_SHORT_SALE_SUSPENSION = "margin_short_sale_suspension"
    MARGIN_MAINTENANCE_RATIO = "margin_maintenance_ratio"
    DAILY_SHORT_SALE_BALANCES = "daily_short_sale_balances"
    SECURITIES_LENDING = "securities_lending"
    LOAN_COLLATERAL_BALANCE = "loan_collateral_balance"
    DISPOSITION_SECURITIES = "disposition_securities"
    # TWSE/TPEX 注意股+處置 whole-market announcement feeds (sources/twse_openapi.py,
    # sources/tpex_openapi.py) — distinct from DISPOSITION_SECURITIES above (FinMind's
    # per-symbol TaiwanStockDispositionSecuritiesPeriod). Backs the 處置王 board.
    ATTENTION_STOCKS = "attention_stocks"
    DISPOSITION_EVENTS = "disposition_events"
    # MOPS 自結損益公告 (t138sb01, self-assessed preliminary P&L) — bounded per-symbol only (the
    # upstream form has no whole-market date-range query, only one market/period at a time), used
    # by the 處置王 board's own roster, never a live whole-market scan. Backs the board's 4th
    # bucket (自結發布).
    SELF_ASSESSED_EARNINGS = "self_assessed_earnings"

    # --- Shareholding / distribution ---
    SHAREHOLDING = "shareholding"
    SHAREHOLDING_DISTRIBUTION = "shareholding_distribution"
    FOREIGN_SHAREHOLDING = "foreign_shareholding"
    MARKET_VALUE = "market_value"
    MARKET_VALUE_WEIGHT = "market_value_weight"

    # --- TDCC (Taiwan Depository & Clearing Corporation) ---
    TDCC_SHAREHOLDING_DISTRIBUTION = "tdcc_shareholding_distribution"
    TDCC_MONTHLY_CUSTODY_ACCOUNTS = "tdcc_monthly_custody_accounts"
    TDCC_PLEDGED_CUSTODY_SHARES = "tdcc_pledged_custody_shares"
    TDCC_EVOTING_SCHEDULE = "tdcc_evoting_schedule"
    TDCC_OFFSHORE_FUND_NAV = "tdcc_offshore_fund_nav"
    TDCC_SECURITIES_INFO = "tdcc_securities_info"

    # --- Analyst / consensus / research ---
    ANALYST_CONSENSUS = "analyst_consensus"
    ESG = "esg"
    PROFILE = "profile"
    NEWS = "news"
    STOCK_NEWS = "stock_news"

    # --- Reference / symbol lookup ---
    STOCK_INFO = "stock_info"
    STOCK_INFO_WITH_WARRANT = "stock_info_with_warrant"
    SYMBOL_LOOKUP = "symbol_lookup"
    SYMBOL_SEARCH = "symbol_search"
    SECURITIES_TRADER_INFO = "securities_trader_info"
    MARKET_STATUS = "market_status"
    TRADING_CALENDAR = "trading_calendar"

    # --- Calendar / events ---
    CALENDAR_EVENTS = "calendar_events"
    EARNINGS_CALENDAR = "earnings_calendar"
    ECONOMIC_CALENDAR = "economic_calendar"
    IPO_CALENDAR = "ipo_calendar"

    # --- ETF / fund ---
    ETF_HOLDINGS = "etf_holdings"
    ETF_EXPENSE_RATIO = "etf_expense_ratio"
    ETF_NAV = "etf_nav"
    ETF_AUM = "etf_aum"
    ETF_THEME_TAGS = "etf_theme_tags"
    ETF_TRAILING_RETURNS = "etf_trailing_returns"
    ACTIVE_ETF_HOLDING = "active_etf_holding"
    ACTIVE_ETF_HOLDING_CHANGE = "active_etf_holding_change"
    ACTIVE_ETF_INFO = "active_etf_info"
    FUND_HOLDINGS = "fund_holdings"

    # --- Convertible bonds (CB) ---
    CB_DAILY = "cb_daily"
    CB_DAILY_OVERVIEW = "cb_daily_overview"
    CB_INFO = "cb_info"
    CB_INSTITUTIONAL_FLOW = "cb_institutional_flow"
    CB_MONTHLY_ANALYSIS = "cb_monthly_analysis"
    CB_PUT_PROVISION = "cb_put_provision"

    # --- Asset swap ---
    ASSET_SWAP_FIXED_INCOME_DAILY = "asset_swap_fixed_income_daily"
    ASSET_SWAP_OPTION_DAILY = "asset_swap_option_daily"

    # --- Futures / options / derivatives (TAIFEX) ---
    DERIVATIVES_OI = "derivatives_oi"
    FUTOPT_INFO = "futopt_info"
    FUTOPT_TICK_INFO = "futopt_tick_info"
    FUTURES_DAILY = "futures_daily"
    FUTURES_DEALER_VOLUME = "futures_dealer_volume"
    FUTURES_FINAL_SETTLEMENT_PRICE = "futures_final_settlement_price"
    FUTURES_INSTITUTIONAL_FLOW = "futures_institutional_flow"
    FUTURES_INSTITUTIONAL_FLOW_AFTER_HOURS = "futures_institutional_flow_after_hours"
    FUTURES_OI_LARGE_TRADERS = "futures_oi_large_traders"
    FUTURES_SNAPSHOT = "futures_snapshot"
    FUTURES_SPREAD_TICK = "futures_spread_tick"
    FUTURES_SPREAD_TRADING = "futures_spread_trading"
    FUTURES_TICK = "futures_tick"
    OPTION_DAILY = "option_daily"
    OPTION_DEALER_VOLUME = "option_dealer_volume"
    OPTION_FINAL_SETTLEMENT_PRICE = "option_final_settlement_price"
    OPTION_INSTITUTIONAL_FLOW = "option_institutional_flow"
    OPTION_INSTITUTIONAL_FLOW_AFTER_HOURS = "option_institutional_flow_after_hours"
    OPTION_OI_LARGE_TRADERS = "option_oi_large_traders"
    OPTION_TICK = "option_tick"
    OPTION_VIX = "option_vix"
    OPTIONS_CHAIN = "options_chain"
    OPTIONS_SNAPSHOT = "options_snapshot"
    INDEX_5S_SNAPSHOT = "index_5s_snapshot"
    TAIEX_5S_INDEX = "taiex_5s_index"

    # --- Foreign markets (US / UK / Europe / Japan) ---
    US_STOCK_INFO = "us_stock_info"
    US_STOCK_PRICE = "us_stock_price"
    US_STOCK_PRICE_MINUTE = "us_stock_price_minute"
    UK_STOCK_INFO = "uk_stock_info"
    UK_STOCK_PRICE = "uk_stock_price"
    EUROPE_STOCK_INFO = "europe_stock_info"
    EUROPE_STOCK_PRICE = "europe_stock_price"
    JAPAN_STOCK_INFO = "japan_stock_info"
    JAPAN_STOCK_PRICE = "japan_stock_price"

    # --- Macro / sector ---
    MACRO = "macro"
    SECTOR_CLASS = "sector_class"
    SECTOR_OVERVIEW = "sector_overview"
    INDUSTRY_OVERVIEW = "industry_overview"
    INDUSTRY_CHAIN_CLASSIFICATION = "industry_chain_classification"
    INDUSTRY_CHAIN_MONEY_FLOW = "industry_chain_money_flow"
    CENTRAL_BANK_RATE = "central_bank_rate"
    EXCHANGE_RATE = "exchange_rate"
    GOVERNMENT_BOND_YIELD = "government_bond_yield"
    GOLD_PRICE = "gold_price"
    CRUDE_OIL_PRICES = "crude_oil_prices"
    FEAR_GREED_INDEX = "fear_greed_index"

    # --- Warrant ---
    WARRANT_UNDERLYING = "warrant_underlying"
