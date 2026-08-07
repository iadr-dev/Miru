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
    EU = "EU"  # continental-Europe equities (e.g. Euronext Amsterdam) — no single-country home
    GLOBAL = "GLOBAL"  # datasets with no single-country home at all (multi-country central-bank
    # rate panels, world commodity benchmark prices) — distinct from a market-specific gap;
    # using e.g. TW here would misleadingly imply Taiwan is the data's country of origin


class Tier(StrEnum):
    """User subscription tier — gates paid data + throughput only, never a recommendation."""

    FREE = "free"
    PREMIUM = "premium"
    PRO = "pro"


class Capability(StrEnum):
    """What callers request — never a source id."""

    PRICE_EOD = "price_eod"
    PRICE_INTRADAY = "price_intraday"
    FINANCIALS = "financials"
    DIVIDENDS = "dividends"
    CORPORATE_ACTIONS = "corporate_actions"
    INSTITUTIONAL_FLOW = "institutional_flow"
    MARGIN_SHORT = "margin_short"
    SHAREHOLDING = "shareholding"
    CHIP_BROKER = "chip_broker"
    ANALYST_CONSENSUS = "analyst_consensus"
    ESG = "esg"
    PROFILE = "profile"
    NEWS = "news"
    CALENDAR_EVENTS = "calendar_events"
    ETF_HOLDINGS = "etf_holdings"
    DERIVATIVES_OI = "derivatives_oi"
    MACRO = "macro"
    SECTOR_CLASS = "sector_class"
    ACTIVE_ETF_INFO = "active_etf_info"
    ACTIVE_ETF_HOLDING = "active_etf_holding"
    ACTIVE_ETF_HOLDING_CHANGE = "active_etf_holding_change"

    # Taiwan Market - Technical
    STOCK_INFO = "stock_info"
    STOCK_INFO_WITH_WARRANT = "stock_info_with_warrant"
    WARRANT_UNDERLYING = "warrant_underlying"
    TRADING_CALENDAR = "trading_calendar"
    PRICE_EOD_ADJUSTED = "price_eod_adjusted"
    PRICE_TICK = "price_tick"
    VALUATION_RATIOS = "valuation_ratios"
    ORDER_BOOK_STATS = "order_book_stats"
    TAIEX_5S_INDEX = "taiex_5s_index"
    DAY_TRADING = "day_trading"
    TOTAL_RETURN_INDEX = "total_return_index"
    TEN_YEAR_PRICE = "ten_year_price"
    INTRADAY_KBAR = "intraday_kbar"
    PRICE_WEEKLY = "price_weekly"
    PRICE_MONTHLY = "price_monthly"
    INDEX_5S_SNAPSHOT = "index_5s_snapshot"
    TRADING_SUSPENSION = "trading_suspension"
    DAY_TRADING_SUSPENSION = "day_trading_suspension"
    PRICE_LIMIT = "price_limit"

    # Taiwan Market - Chip / Institutional
    MARGIN_SHORT_TOTAL_MARKET = "margin_short_total_market"
    INSTITUTIONAL_FLOW_WIDE = "institutional_flow_wide"
    INSTITUTIONAL_FLOW_TOTAL_MARKET = "institutional_flow_total_market"
    FOREIGN_SHAREHOLDING = "foreign_shareholding"
    SHAREHOLDING_DISTRIBUTION = "shareholding_distribution"
    SECURITIES_LENDING = "securities_lending"
    MARGIN_SHORT_SALE_SUSPENSION = "margin_short_sale_suspension"
    DAILY_SHORT_SALE_BALANCES = "daily_short_sale_balances"
    SECURITIES_TRADER_INFO = "securities_trader_info"
    WARRANT_CHIP_BROKER = "warrant_chip_broker"
    GOVERNMENT_BANK_BUYSELL = "government_bank_buysell"
    MARGIN_MAINTENANCE_RATIO = "margin_maintenance_ratio"
    CHIP_BROKER_AGG = "chip_broker_agg"
    BLOCK_TRADE_BROKER_REPORT = "block_trade_broker_report"
    BLOCK_TRADE = "block_trade"
    LOAN_COLLATERAL_BALANCE = "loan_collateral_balance"
    INDUSTRY_CHAIN_MONEY_FLOW = "industry_chain_money_flow"
    DISPOSITION_SECURITIES = "disposition_securities"

    # Taiwan Market - Fundamental
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW_STATEMENT = "cash_flow_statement"
    DIVIDEND_RESULT = "dividend_result"
    MONTH_REVENUE = "month_revenue"
    CAPITAL_REDUCTION_REFERENCE_PRICE = "capital_reduction_reference_price"
    MARKET_VALUE = "market_value"
    STOCK_DELISTING = "stock_delisting"
    MARKET_VALUE_WEIGHT = "market_value_weight"
    SPLIT_PRICE = "split_price"
    PAR_VALUE_CHANGE = "par_value_change"

    # Taiwan Market - Derivative
    FUTOPT_INFO = "futopt_info"
    FUTURES_DAILY = "futures_daily"
    OPTION_DAILY = "option_daily"
    FUTURES_TICK = "futures_tick"
    FUTURES_SPREAD_TICK = "futures_spread_tick"
    OPTION_TICK = "option_tick"
    FUTURES_INSTITUTIONAL_FLOW = "futures_institutional_flow"
    OPTION_INSTITUTIONAL_FLOW = "option_institutional_flow"
    FUTURES_INSTITUTIONAL_FLOW_AFTER_HOURS = "futures_institutional_flow_after_hours"
    OPTION_INSTITUTIONAL_FLOW_AFTER_HOURS = "option_institutional_flow_after_hours"
    FUTURES_DEALER_VOLUME = "futures_dealer_volume"
    OPTION_DEALER_VOLUME = "option_dealer_volume"
    FUTURES_OI_LARGE_TRADERS = "futures_oi_large_traders"
    OPTION_OI_LARGE_TRADERS = "option_oi_large_traders"
    FUTURES_SPREAD_TRADING = "futures_spread_trading"
    FUTURES_FINAL_SETTLEMENT_PRICE = "futures_final_settlement_price"
    OPTION_FINAL_SETTLEMENT_PRICE = "option_final_settlement_price"
    OPTION_VIX = "option_vix"
    ASSET_SWAP_FIXED_INCOME_DAILY = "asset_swap_fixed_income_daily"
    ASSET_SWAP_OPTION_DAILY = "asset_swap_option_daily"

    # Taiwan Market - Real-Time
    TICK_SNAPSHOT = "tick_snapshot"
    FUTOPT_TICK_INFO = "futopt_tick_info"
    FUTURES_SNAPSHOT = "futures_snapshot"
    OPTIONS_SNAPSHOT = "options_snapshot"

    # Taiwan Market - Convertible Bond
    CB_INFO = "cb_info"
    CB_DAILY = "cb_daily"
    CB_INSTITUTIONAL_FLOW = "cb_institutional_flow"
    CB_DAILY_OVERVIEW = "cb_daily_overview"
    CB_MONTHLY_ANALYSIS = "cb_monthly_analysis"
    CB_PUT_PROVISION = "cb_put_provision"

    # Taiwan Market - Others
    STOCK_NEWS = "stock_news"
    BUSINESS_INDICATOR = "business_indicator"
    INDUSTRY_CHAIN_CLASSIFICATION = "industry_chain_classification"

    # International Markets
    US_STOCK_INFO = "us_stock_info"
    US_STOCK_PRICE = "us_stock_price"
    US_STOCK_PRICE_MINUTE = "us_stock_price_minute"
    UK_STOCK_INFO = "uk_stock_info"
    UK_STOCK_PRICE = "uk_stock_price"
    EUROPE_STOCK_INFO = "europe_stock_info"
    EUROPE_STOCK_PRICE = "europe_stock_price"
    JAPAN_STOCK_INFO = "japan_stock_info"
    JAPAN_STOCK_PRICE = "japan_stock_price"

    # Global Economic Data
    EXCHANGE_RATE = "exchange_rate"
    CENTRAL_BANK_RATE = "central_bank_rate"
    GOLD_PRICE = "gold_price"
    CRUDE_OIL_PRICES = "crude_oil_prices"
    GOVERNMENT_BOND_YIELD = "government_bond_yield"
    FEAR_GREED_INDEX = "fear_greed_index"

    # yfinance-native — no existing FinMind/gov equivalent (options, holders, cross-market
    # search/resolution, calendars, sector/industry aggregates, market status, fund holdings).
    # PRICE_EOD/FINANCIALS/BALANCE_SHEET/CASH_FLOW_STATEMENT/DIVIDENDS/SPLIT_PRICE/
    # ANALYST_CONSENSUS/ESG/SECTOR_CLASS are reused as-is for non-TW markets — never
    # duplicated under a yfinance-only name.
    OPTIONS_CHAIN = "options_chain"
    INSTITUTIONAL_HOLDERS = "institutional_holders"
    INSIDER_ACTIVITY = "insider_activity"
    SYMBOL_SEARCH = "symbol_search"
    SYMBOL_LOOKUP = "symbol_lookup"
    EARNINGS_CALENDAR = "earnings_calendar"
    IPO_CALENDAR = "ipo_calendar"
    ECONOMIC_CALENDAR = "economic_calendar"
    SECTOR_OVERVIEW = "sector_overview"
    INDUSTRY_OVERVIEW = "industry_overview"
    MARKET_STATUS = "market_status"
    FUND_HOLDINGS = "fund_holdings"

    # Screener query concepts. A screener takes a *query* over a whole universe, not a
    # `(capability, symbol)` pair, so these are never declared in `supports()` and never
    # dispatched through `SourceAdapter.fetch()`/`dataaccess/router.py` — they exist purely to
    # name the concept consistently in logs/journal entries and for a future screener tool to
    # reference. The actual execution path is `sources.yfinance.run_screener`, a query-shaped
    # carve-out analogous to `batch_download`/`fetch_whole_market`.
    EQUITY_SCREENER = "equity_screener"
    ETF_SCREENER = "etf_screener"
    FUND_SCREENER = "fund_screener"
