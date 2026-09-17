"""FinMind Chip/Institutional-category datasets — margin/short-sale balances, three-major-
institutional-investor flow (narrow and wide forms), foreign shareholding, securities lending,
branch(分點)/block-trade broker reports, loan-collateral balances, and the industry-chain
money-flow aggregate. 20 datasets.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _as_date_str, _as_float, _as_int, _as_optional_str, _DatasetConfig, _row_symbol


def _map_margin_short(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "margin_purchase_buy": _as_int(row.get("MarginPurchaseBuy")),
                "margin_purchase_cash_repayment": _as_int(row.get("MarginPurchaseCashRepayment")),
                "margin_purchase_limit": _as_int(row.get("MarginPurchaseLimit")),
                "margin_purchase_sell": _as_int(row.get("MarginPurchaseSell")),
                "margin_purchase_today_balance": _as_int(row.get("MarginPurchaseTodayBalance")),
                "margin_purchase_yesterday_balance": _as_int(
                    row.get("MarginPurchaseYesterdayBalance")
                ),
                "note": row.get("Note") or None,
                "offset_loan_and_short": _as_int(row.get("OffsetLoanAndShort")),
                "short_sale_buy": _as_int(row.get("ShortSaleBuy")),
                "short_sale_cash_repayment": _as_int(row.get("ShortSaleCashRepayment")),
                "short_sale_limit": _as_int(row.get("ShortSaleLimit")),
                "short_sale_sell": _as_int(row.get("ShortSaleSell")),
                "short_sale_today_balance": _as_int(row.get("ShortSaleTodayBalance")),
                "short_sale_yesterday_balance": _as_int(row.get("ShortSaleYesterdayBalance")),
            }
        )
    return rows_out


def _map_margin_short_total_market(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockTotalMarginPurchaseShortSale` — market-level per-category total, no
    `stock_id` column."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        rows_out.append(
            {
                "market": Market.TW.value,
                "trade_date": _as_date_str(row["date"]),
                "name": str(row.get("name", "")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
                "today_balance": _as_int(row.get("TodayBalance")),
                "yesterday_balance": _as_int(row.get("YesBalance")),
                "return_": _as_float(row.get("Return")),
            }
        )
    return rows_out


def _map_institutional_flow(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    flows: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        flows.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "investor": str(row.get("name", "")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
            }
        )
    return flows


def _map_institutional_flow_wide(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockInstitutionalInvestorsBuySellWide` — a category is 0 in eras where it did
    not exist (Dealer_self/Dealer_Hedging start 2014-12-01, Foreign_Dealer_Self starts
    2018-01-15) — passed through as-is, no back-fill or estimation performed here."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "foreign_investor_buy": _as_int(row.get("Foreign_Investor_buy")),
                "foreign_investor_sell": _as_int(row.get("Foreign_Investor_sell")),
                "foreign_dealer_self_buy": _as_int(row.get("Foreign_Dealer_Self_buy")),
                "foreign_dealer_self_sell": _as_int(row.get("Foreign_Dealer_Self_sell")),
                "investment_trust_buy": _as_int(row.get("Investment_Trust_buy")),
                "investment_trust_sell": _as_int(row.get("Investment_Trust_sell")),
                "dealer_buy": _as_int(row.get("Dealer_buy")),
                "dealer_sell": _as_int(row.get("Dealer_sell")),
                "dealer_self_buy": _as_int(row.get("Dealer_self_buy")),
                "dealer_self_sell": _as_int(row.get("Dealer_self_sell")),
                "dealer_hedging_buy": _as_int(row.get("Dealer_Hedging_buy")),
                "dealer_hedging_sell": _as_int(row.get("Dealer_Hedging_sell")),
            }
        )
    return rows_out


def _map_institutional_flow_total_market(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockTotalInstitutionalInvestors` — market-level per-category total, no
    `stock_id` column."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        rows_out.append(
            {
                "market": Market.TW.value,
                "trade_date": _as_date_str(row["date"]),
                "name": str(row.get("name", "")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
            }
        )
    return rows_out


def _map_foreign_shareholding(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "stock_name": str(row.get("stock_name", "")),
                "international_code": str(row.get("InternationalCode", "")),
                "foreign_investment_remaining_shares": _as_int(
                    row.get("ForeignInvestmentRemainingShares")
                ),
                "foreign_investment_shares": _as_int(row.get("ForeignInvestmentShares")),
                "foreign_investment_remain_ratio": _as_float(
                    row.get("ForeignInvestmentRemainRatio")
                ),
                "foreign_investment_shares_ratio": _as_float(
                    row.get("ForeignInvestmentSharesRatio")
                ),
                "foreign_investment_upper_limit_ratio": _as_float(
                    row.get("ForeignInvestmentUpperLimitRatio")
                ),
                "chinese_investment_upper_limit_ratio": _as_float(
                    row.get("ChineseInvestmentUpperLimitRatio")
                ),
                "number_of_shares_issued": _as_int(row.get("NumberOfSharesIssued")),
                "recently_declare_date": row.get("RecentlyDeclareDate") or None,
                "note": row.get("note") or None,
            }
        )
    return rows_out


def _map_shareholding_distribution(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "holding_shares_level": str(row.get("HoldingSharesLevel", "")),
                "people": _as_int(row.get("people")),
                "percent": _as_float(row.get("percent")),
                "unit": _as_int(row.get("unit")),
            }
        )
    return rows_out


def _map_securities_lending(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "transaction_type": str(row.get("transaction_type", "")),
                "volume": _as_int(row.get("volume")),
                "fee_rate": _as_float(row.get("fee_rate")),
                "close": _as_float(row.get("close")),
                "original_return_date": _as_optional_str(row.get("original_return_date")),
                "original_lending_period": _as_optional_str(row.get("original_lending_period")),
            }
        )
    return rows_out


def _map_margin_short_sale_suspension(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": _as_date_str(row["date"]),
                "end_date": row.get("end_date"),
                "reason": str(row.get("reason", "")),
            }
        )
    return rows_out


def _map_daily_short_sale_balances(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "margin_short_sales_previous_day_balance": _as_int(
                    row.get("MarginShortSalesPreviousDayBalance")
                ),
                "margin_short_sales_short_sales": _as_int(row.get("MarginShortSalesShortSales")),
                "margin_short_sales_short_covering": _as_int(
                    row.get("MarginShortSalesShortCovering")
                ),
                "margin_short_sales_stock_redemption": _as_int(
                    row.get("MarginShortSalesStockRedemption")
                ),
                "margin_short_sales_current_day_balance": _as_int(
                    row.get("MarginShortSalesCurrentDayBalance")
                ),
                "margin_short_sales_quota": _as_int(row.get("MarginShortSalesQuota")),
                "sbl_short_sales_previous_day_balance": _as_int(
                    row.get("SBLShortSalesPreviousDayBalance")
                ),
                "sbl_short_sales_short_sales": _as_int(row.get("SBLShortSalesShortSales")),
                "sbl_short_sales_returns": _as_int(row.get("SBLShortSalesReturns")),
                "sbl_short_sales_adjustments": _as_int(row.get("SBLShortSalesAdjustments")),
                "sbl_short_sales_current_day_balance": _as_int(
                    row.get("SBLShortSalesCurrentDayBalance")
                ),
                "sbl_short_sales_quota": _as_int(row.get("SBLShortSalesQuota")),
                "sbl_short_sales_short_covering": _as_int(row.get("SBLShortSalesShortCovering")),
            }
        )
    return rows_out


def _map_securities_trader_info(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanSecuritiesTraderInfo` — a broker directory, not symbol-scoped."""
    return [
        {
            "market": Market.TW.value,
            "trader_id": str(row.get("securities_trader_id", "")),
            "trader_name": str(row.get("securities_trader", "")),
            "as_of": row.get("date"),
            "address": row.get("address") or None,
            "phone": row.get("phone") or None,
        }
        for row in rows
    ]


def _map_chip_broker(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    branches: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        branches.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "broker_id": str(row.get("securities_trader_id", "")),
                "broker_name": str(row.get("securities_trader", "")),
                "price": _as_float(row.get("price")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
            }
        )
    return branches


def _map_government_bank_buysell(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`no_data_id_param=True` (confirmed empirically — the endpoint 400s if `data_id` is
    present at all) means even a per-symbol `fetch()` call gets EVERY stock's row back for the
    requested day — there is no server-side symbol filter to lean on. `_row_symbol` alone
    (as every other whole-market-capable mapper in this module uses it) would mislabel every
    row as the caller's `sym` instead of filtering, which is exactly the bug a live symbol/date
    probe caught (a per-symbol request always returning the same ~13k-row whole-market count
    regardless of which symbol or date was asked for). Filter to the caller's own `stock_id`
    first — same real per-row field `_row_symbol`'s own whole-market (`sym=None`) branch reads
    off each row already, just applied as a predicate here instead of a label."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        if sym is not None and str(row.get("stock_id", "")) != sym.id:
            continue
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "buy_amount": _as_float(row.get("buy_amount")),
                "sell_amount": _as_float(row.get("sell_amount")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
                "bank_name": str(row.get("bank_name", "")),
            }
        )
    return rows_out


def _map_margin_maintenance_ratio(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanTotalExchangeMarginMaintenance` — market-level, no `stock_id` column."""
    return [
        {
            "market": Market.TW.value,
            "trade_date": _as_date_str(row["date"]),
            "total_exchange_margin_maintenance": _as_float(
                row.get("TotalExchangeMarginMaintenance")
            ),
        }
        for row in rows
    ]


def _map_chip_broker_agg(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "broker_id": str(row.get("securities_trader_id", "")),
                "broker_name": str(row.get("securities_trader", "")),
                "buy_volume": _as_int(row.get("buy_volume")),
                "sell_volume": _as_int(row.get("sell_volume")),
                "buy_price": _as_float(row.get("buy_price")),
                "sell_price": _as_float(row.get("sell_price")),
            }
        )
    return rows_out


def _map_block_trade_broker_report(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "broker_id": str(row.get("securities_trader_id", "")),
                "broker_name": str(row.get("securities_trader", "")),
                "price": _as_float(row.get("price")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
                "trade_type": str(row.get("trade_type", "")),
            }
        )
    return rows_out


def _map_block_trade(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "trade_type": str(row.get("trade_type", "")),
                "price": _as_float(row.get("price")),
                "volume": _as_int(row.get("volume")),
                "trading_money": _as_float(row.get("trading_money")),
            }
        )
    return rows_out


def _map_loan_collateral_balance(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "exchange_market": str(row.get("market", "")),
                "margin_previous_day_balance": _as_int(row.get("MarginPreviousDayBalance")),
                "margin_buy": _as_int(row.get("MarginBuy")),
                "margin_sell": _as_int(row.get("MarginSell")),
                "margin_cash_redemption": _as_int(row.get("MarginCashRedemption")),
                "margin_current_day_balance": _as_int(row.get("MarginCurrentDayBalance")),
                "margin_next_day_quota": _as_int(row.get("MarginNextDayQuota")),
                "securities_firm_loan_previous_day_balance": _as_int(
                    row.get("SecuritiesFirmLoanPreviousDayBalance")
                ),
                "securities_firm_loan_buy": _as_int(row.get("SecuritiesFirmLoanBuy")),
                "securities_firm_loan_sell": _as_int(row.get("SecuritiesFirmLoanSell")),
                "securities_firm_loan_cash_redemption": _as_int(
                    row.get("SecuritiesFirmLoanCashRedemption")
                ),
                "securities_firm_loan_replacement": _as_int(
                    row.get("SecuritiesFirmLoanReplacement")
                ),
                "securities_firm_loan_current_day_balance": _as_int(
                    row.get("SecuritiesFirmLoanCurrentDayBalance")
                ),
                "securities_firm_loan_next_day_quota": _as_int(
                    row.get("SecuritiesFirmLoanNextDayQuota")
                ),
                "unrestricted_loan_previous_day_balance": _as_int(
                    row.get("UnrestrictedLoanPreviousDayBalance")
                ),
                "unrestricted_loan_buy": _as_int(row.get("UnrestrictedLoanBuy")),
                "unrestricted_loan_sell": _as_int(row.get("UnrestrictedLoanSell")),
                "unrestricted_loan_cash_redemption": _as_int(
                    row.get("UnrestrictedLoanCashRedemption")
                ),
                "unrestricted_loan_replacement": _as_int(row.get("UnrestrictedLoanReplacement")),
                "unrestricted_loan_current_day_balance": _as_int(
                    row.get("UnrestrictedLoanCurrentDayBalance")
                ),
                "unrestricted_loan_next_day_quota": _as_int(
                    row.get("UnrestrictedLoanNextDayQuota")
                ),
                "securities_finance_secured_loan_previous_day_balance": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanPreviousDayBalance")
                ),
                "securities_finance_secured_loan_buy": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanBuy")
                ),
                "securities_finance_secured_loan_sell": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanSell")
                ),
                "securities_finance_secured_loan_cash_redemption": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanCashRedemption")
                ),
                "securities_finance_secured_loan_replacement": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanReplacement")
                ),
                "securities_finance_secured_loan_current_day_balance": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanCurrentDayBalance")
                ),
                "securities_finance_secured_loan_next_day_quota": _as_int(
                    row.get("SecuritiesFinanceSecuredLoanNextDayQuota")
                ),
                "settlement_margin_previous_day_balance": _as_int(
                    row.get("SettlementMarginPreviousDayBalance")
                ),
                "settlement_margin_buy": _as_int(row.get("SettlementMarginBuy")),
                "settlement_margin_sell": _as_int(row.get("SettlementMarginSell")),
                "settlement_margin_cash_redemption": _as_int(
                    row.get("SettlementMarginCashRedemption")
                ),
                "settlement_margin_replacement": _as_int(row.get("SettlementMarginReplacement")),
                "settlement_margin_current_day_balance": _as_int(
                    row.get("SettlementMarginCurrentDayBalance")
                ),
                "settlement_margin_next_day_quota": _as_int(
                    row.get("SettlementMarginNextDayQuota")
                ),
            }
        )
    return rows_out


def _map_industry_chain_money_flow(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockIndustryChainMoneyFlow` — market-level; an empty `sub_industry` marks an
    industry-chain total row. Total rows do NOT equal the sum of their sub-industry rows (a
    stock can belong to multiple sub-industries within one chain), and one stock can belong to
    multiple chains, so `trading_money_pct` across chains can sum to more than 100%."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        rows_out.append(
            {
                "market": Market.TW.value,
                "trade_date": _as_date_str(row["date"]),
                "industry": str(row.get("industry", "")),
                "sub_industry": str(row.get("sub_industry", "")),
                "stock_count": _as_int(row.get("stock_count")),
                "trading_volume": _as_int(row.get("trading_volume")),
                "trading_money": _as_float(row.get("trading_money")),
                "trading_money_pct": _as_float(row.get("trading_money_pct")),
            }
        )
    return rows_out


def _map_disposition_securities(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": _as_date_str(row["date"]),
                "stock_name": str(row.get("stock_name", "")),
                "disposition_cnt": _as_int(row.get("disposition_cnt")),
                "condition": str(row.get("condition", "")),
                "measure": str(row.get("measure", "")),
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end"),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.MARGIN_SHORT: _DatasetConfig(
        dataset="TaiwanStockMarginPurchaseShortSale",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily margin-purchase/short-sale balance; updates Mon-Fri ~21:00.",
        row_key="rows",
        row_mapper=_map_margin_short,
        whole_market_ok=True,
    ),
    Capability.MARGIN_SHORT_TOTAL_MARKET: _DatasetConfig(
        dataset="TaiwanStockTotalMarginPurchaseShortSale",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Whole-market margin/short-sale total by category; updates Mon-Fri ~21:00; no "
        "`data_id` param exists.",
        row_key="rows",
        row_mapper=_map_margin_short_total_market,
        whole_market_ok=True,
    ),
    Capability.INSTITUTIONAL_FLOW: _DatasetConfig(
        dataset="TaiwanStockInstitutionalInvestorsBuySell",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Three-major-institutional-investor buy/sell; published post-close, T+1.",
        row_key="rows",
        row_mapper=_map_institutional_flow,
        whole_market_ok=True,
    ),
    Capability.CHIP_BROKER: _DatasetConfig(
        dataset="TaiwanStockTradingDailyReport",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier branch(分點) trading report; single trading day, published "
        "post-close (~21:00), not live.",
        row_key="rows",
        row_mapper=_map_chip_broker,
        date_param="date",
        special_endpoint="/taiwan_stock_trading_daily_report",
    ),
    Capability.INSTITUTIONAL_FLOW_WIDE: _DatasetConfig(
        dataset="TaiwanStockInstitutionalInvestorsBuySellWide",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Same data as institutional_flow reshaped wide (one column per investor "
        "category); a category reads 0 in eras it did not exist. Published post-close, T+1.",
        row_key="rows",
        row_mapper=_map_institutional_flow_wide,
        whole_market_ok=True,
    ),
    Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET: _DatasetConfig(
        dataset="TaiwanStockTotalInstitutionalInvestors",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Whole-market three-major-institutional-investor buy/sell total by category; "
        "updates Mon-Fri ~15:00; no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_institutional_flow_total_market,
        whole_market_ok=True,
    ),
    Capability.FOREIGN_SHAREHOLDING: _DatasetConfig(
        dataset="TaiwanStockShareholding",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily foreign-shareholding-limit/ratio row; updates Mon-Fri ~21:00.",
        row_key="rows",
        row_mapper=_map_foreign_shareholding,
        whole_market_ok=True,
    ),
    Capability.SHAREHOLDING_DISTRIBUTION: _DatasetConfig(
        dataset="TaiwanStockHoldingSharesPer",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier shareholding-level distribution table.",
        row_key="rows",
        row_mapper=_map_shareholding_distribution,
        whole_market_ok=True,
    ),
    Capability.SECURITIES_LENDING: _DatasetConfig(
        dataset="TaiwanStockSecuritiesLending",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily securities-lending transaction row; updates Mon-Fri ~15:00.",
        row_key="rows",
        row_mapper=_map_securities_lending,
        whole_market_ok=True,
    ),
    Capability.MARGIN_SHORT_SALE_SUSPENSION: _DatasetConfig(
        dataset="TaiwanStockMarginShortSaleSuspension",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Short-sale-suspension announcement stream; event-driven, not a daily-clock-"
        "time market snapshot. Updates Mon-Fri ~21:00.",
        row_key="rows",
        row_mapper=_map_margin_short_sale_suspension,
        whole_market_ok=True,
        # Rare, irregular event for any one symbol — a 30-day window frequently has no
        # occurrence at all (confirmed empirically: 0 rows at 30d for 2330).
        default_lookback_days=450,
    ),
    Capability.DAILY_SHORT_SALE_BALANCES: _DatasetConfig(
        dataset="TaiwanDailyShortSaleBalances",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily credit-limit short-sale-quota/balance row (margin + securities-lending); "
        "updates Mon-Fri ~21:00.",
        row_key="rows",
        row_mapper=_map_daily_short_sale_balances,
        whole_market_ok=True,
    ),
    Capability.SECURITIES_TRADER_INFO: _DatasetConfig(
        dataset="TaiwanSecuritiesTraderInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-market broker/securities-trader directory reference table; no `data_id` "
        "param exists.",
        row_key="rows",
        row_mapper=_map_securities_trader_info,
        whole_market_ok=True,
    ),
    Capability.WARRANT_CHIP_BROKER: _DatasetConfig(
        dataset="TaiwanStockWarrantTradingDailyReport",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier warrant branch(分點) trading report; published post-close, not live.",
        row_key="rows",
        row_mapper=_map_chip_broker,
        # Confirmed empirically: 400s ("size too large, we only send one day data") if
        # end_date is present at all — single trading day per request only.
        no_end_date_param=True,
    ),
    Capability.GOVERNMENT_BANK_BUYSELL: _DatasetConfig(
        # The FinMind docs' own markdown heading has a typo (lowercase "s" in "stock") — the
        # real API rejects that spelling with a 422 enum-validation error and requires this
        # capitalization (confirmed empirically against the live endpoint).
        dataset="TaiwanStockGovernmentBankBuySell",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier eight-government-bank(八大行庫) buy/sell row; updates Mon-Fri "
        "~23:30, not live.",
        row_key="rows",
        row_mapper=_map_government_bank_buysell,
        # Confirmed empirically against the live endpoint: this dataset uses `start_date`
        # (the `_DatasetConfig` default `date_param`), NOT `date` — the FinMind docs describe
        # it as single-day-scoped, but the real 400 body when `date` is sent instead
        # ("start_date parameter is missing") proves `start_date` is the one real upstream
        # param name. `end_date` still 400s if present at all ("size too large, we only send
        # one day data") — so a single `start_date` with no `end_date` is what scopes the
        # response to that one day. `data_id` (a per-stock filter) also 400s if present at
        # all — this dataset returns EVERY symbol's row for the requested day, whole-market,
        # filtered client-side by `GetGovernmentBankFlowsTool`'s own row-mapper/caller instead.
        no_end_date_param=True,
        no_data_id_param=True,
        whole_market_ok=True,
    ),
    Capability.MARGIN_MAINTENANCE_RATIO: _DatasetConfig(
        dataset="TaiwanTotalExchangeMarginMaintenance",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier whole-market margin-maintenance-ratio reading; updates "
        "Mon-Fri ~21:00; no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_margin_maintenance_ratio,
        whole_market_ok=True,
        # Confirmed empirically: the upstream endpoint 400s outright if `data_id` is sent at
        # all, even on the per-symbol `fetch()` path — this dataset has no per-stock filter,
        # market-level only.
        no_data_id_param=True,
    ),
    Capability.CHIP_BROKER_AGG: _DatasetConfig(
        dataset="TaiwanStockTradingDailyReportSecIdAgg",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier per-broker aggregated branch(分點) buy/sell; served by FinMind's "
        "dedicated aggregate endpoint, not the generic data endpoint; published post-close "
        "~21:00, not live.",
        row_key="rows",
        row_mapper=_map_chip_broker_agg,
        special_endpoint="/taiwan_stock_trading_daily_report_secid_agg",
        # KNOWN UNRESOLVED (confirmed empirically, not yet fixed): the live endpoint's own
        # validation contradicts the doc's documented Params line (`data_id`+`start_date`+
        # `end_date`) — a date-range call 400s demanding `securities_trader_id` (undocumented,
        # not this dataset's actual per-symbol identity param), and a single-`date` call 400s
        # with "date parameter is missing" even with `date` present. Needs a live capture of a
        # real successful browser/SDK call to determine the actual required param shape.
    ),
    Capability.BLOCK_TRADE_BROKER_REPORT: _DatasetConfig(
        dataset="TaiwanStockBlockTradingDailyReport",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier block-trade broker-level report; updates Mon-Fri ~21:00. No "
        "documented `data_id` whole-market split — data range starts 2026-04-28.",
        row_key="rows",
        row_mapper=_map_block_trade_broker_report,
        whole_market_ok=True,
        # Confirmed empirically: 400s ("size is too large, we only send one day data") if
        # end_date is present at all — single trading day per request only.
        no_end_date_param=True,
    ),
    Capability.BLOCK_TRADE: _DatasetConfig(
        dataset="TaiwanStockBlockTrade",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier block-trade execution report.",
        row_key="rows",
        row_mapper=_map_block_trade,
        whole_market_ok=True,
    ),
    Capability.LOAN_COLLATERAL_BALANCE: _DatasetConfig(
        dataset="TaiwanStockLoanCollateralBalance",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier loan-collateral balance report (margin + securities-firm-loan + "
        "unrestricted-loan + securities-finance-secured-loan + settlement-margin).",
        row_key="rows",
        row_mapper=_map_loan_collateral_balance,
        whole_market_ok=True,
    ),
    Capability.INDUSTRY_CHAIN_MONEY_FLOW: _DatasetConfig(
        dataset="TaiwanStockIndustryChainMoneyFlow",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier industry-chain money-flow aggregate, derived daily from EOD "
        "prices; no `data_id` param — always whole-market, single trading day per request "
        "(never pass end_date).",
        row_key="rows",
        row_mapper=_map_industry_chain_money_flow,
        whole_market_ok=True,
        no_data_id_param=True,  # confirmed empirically: no per-symbol filter exists upstream.
        no_end_date_param=True,  # confirmed empirically: 400s ("size too large") with end_date.
    ),
    Capability.DISPOSITION_SECURITIES: _DatasetConfig(
        dataset="TaiwanStockDispositionSecuritiesPeriod",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier disposition-securities(處置有價證券) announcement stream; "
        "event-driven, not a daily-clock-time market snapshot.",
        row_key="rows",
        row_mapper=_map_disposition_securities,
        whole_market_ok=True,
        default_lookback_days=450,  # rare event — a 30d window usually has no occurrence.
    ),
}

__all__ = ["DATASETS"]
