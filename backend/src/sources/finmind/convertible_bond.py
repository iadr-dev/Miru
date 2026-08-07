"""FinMind Convertible-Bond-category datasets — the CB reference roster, EOD trading, three-
major-institutional-investor flow, daily overview (including put/early-redemption fields),
monthly custody-balance analysis, and the put(賣回權) provision schedule. All keyed on `cb_id`,
never an equity `stock_id`. 6 datasets.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, TimingClass
from schemas.market import Symbol

from ._shared import _as_float, _as_int, _DatasetConfig, _row_cb_symbol


def _map_cb_info(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("cb_name", "")),
                "initial_date_of_conversion": row.get("InitialDateOfConversion") or None,
                "due_date_of_conversion": row.get("DueDateOfConversion") or None,
                "issuance_amount": row.get("IssuanceAmount"),
            }
        )
    return rows_out


def _map_cb_daily(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("cb_name", "")),
                "trade_date": row["date"],
                "transaction_type": str(row.get("transaction_type", "")),
                "close": _as_float(row.get("close")),
                "change": _as_float(row.get("change")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "number_of_transactions": _as_int(row.get("no_of_transactions")),
                "unit": str(row.get("unit", "")),
                "trading_value": _as_float(row.get("trading_value")),
                "avg_price": _as_float(row.get("avg_price")),
                "next_ref_price": _as_float(row.get("next_ref_price")),
                "next_max_limit": _as_float(row.get("next_max_limit")),
                "next_min_limit": _as_float(row.get("next_min_limit")),
            }
        )
    return rows_out


def _map_cb_institutional_flow(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("cb_name", "")),
                "trade_date": row["date"],
                "foreign_investor_buy": _as_float(row.get("Foreign_Investor_Buy")),
                "foreign_investor_sell": _as_float(row.get("Foreign_Investor_Sell")),
                "foreign_investor_overbuy": _as_float(row.get("Foreign_Investor_Overbuy")),
                "investment_trust_buy": _as_float(row.get("Investment_Trust_Buy")),
                "investment_trust_sell": _as_float(row.get("Investment_Trust_Sell")),
                "investment_trust_overbuy": _as_float(row.get("Investment_Trust_Overbuy")),
                "dealer_self_buy": _as_float(row.get("Dealer_self_Buy")),
                "dealer_self_sell": _as_float(row.get("Dealer_self_Sell")),
                "dealer_self_overbuy": _as_float(row.get("Dealer_self_Overbuy")),
                "total_overbuy": _as_float(row.get("Total_Overbuy")),
            }
        )
    return rows_out


def _map_cb_daily_overview(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockConvertibleBondDailyOverview` — the put/early-redemption fields are populated
    only while a put or early-redemption process is publicly announced for that bond; empty on
    most bonds most days is expected, not missing data."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("cb_name", "")),
                "trade_date": row["date"],
                "initial_date_of_conversion": row.get("InitialDateOfConversion") or None,
                "due_date_of_conversion": row.get("DueDateOfConversion") or None,
                "initial_date_of_stop_conversion": row.get("InitialDateOfStopConversion") or None,
                "due_date_of_stop_conversion": row.get("DueDateOfStopConversion") or None,
                "conversion_price": row.get("ConversionPrice"),
                "next_effective_date_of_conversion_price": row.get(
                    "NextEffectiveDateOfConversionPrice"
                )
                or None,
                "latest_initial_date_of_put": row.get("LatestInitialDateOfPut") or None,
                "latest_due_date_of_put": row.get("LatestDueDateOfPut") or None,
                "latest_put_price": row.get("LatestPutPrice"),
                "initial_date_of_early_redemption": row.get("InitialDateOfEarlyRedemption") or None,
                "due_date_of_early_redemption": row.get("DueDateOfEarlyRedemption") or None,
                "early_redemption_price": row.get("EarlyRedemptionPrice"),
                "date_of_delisted": row.get("DateOfDelisted") or None,
                "issuance_amount": row.get("IssuanceAmount"),
                "outstanding_amount": row.get("OutstandingAmount"),
                "reference_price": row.get("ReferencePrice"),
                "price_of_underlying_stock": row.get("PriceOfUnderlyingStock"),
                "initial_date_of_suspension": row.get("InitialDateOfSuspension") or None,
                "due_date_of_suspension": row.get("DueDateOfSuspension") or None,
                "coupon_rate": row.get("CouponRate"),
            }
        )
    return rows_out


def _map_cb_monthly_analysis(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("cb_name", "")),
                "name_en": str(row.get("cb_name_en", "")),
                "trade_date": row["date"],
                "custody_balance": _as_int(row.get("custody_balance")),
                "last_month_balance": _as_int(row.get("last_month_balance")),
                "change": _as_int(row.get("change")),
                "change_percent": _as_float(row.get("change_percent")),
                "issued_units": _as_int(row.get("issued_units")),
                "custody_accounts": _as_int(row.get("custody_accounts")),
                "pledged_units": _as_int(row.get("pledged_units")),
            }
        )
    return rows_out


def _map_cb_put_provision(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockConvertibleBondPutProvision` — `data_id` is optional; the date range alone
    returns the put schedule of every convertible bond in that range, including announced
    future put dates."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_cb_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "name": str(row.get("cb_name", "")),
                "put_price": _as_float(row.get("PutPrice")),
                "put_yield_rate": _as_float(row.get("PutYieldRate")),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.CB_INFO: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondInfo",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.REFERENCE,
        caveat="Backer/Sponsor-tier whole-roster convertible-bond reference table; `symbol` is "
        "the bond's `cb_id`, not an equity `stock_id`.",
        row_key="rows",
        row_mapper=_map_cb_info,
        whole_market_ok=True,
    ),
    Capability.CB_DAILY: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondDaily",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier convertible-bond EOD trading row; `symbol` is the `cb_id`.",
        row_key="rows",
        row_mapper=_map_cb_daily,
        whole_market_ok=True,
    ),
    Capability.CB_INSTITUTIONAL_FLOW: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondInstitutionalInvestors",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier convertible-bond three-major-institutional-investor "
        "daily buy/sell/overbuy row; `symbol` is the `cb_id`.",
        row_key="rows",
        row_mapper=_map_cb_institutional_flow,
        whole_market_ok=True,
    ),
    Capability.CB_DAILY_OVERVIEW: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondDailyOverview",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier convertible-bond daily overview row; `symbol` is the "
        "`cb_id`. Put/early-redemption fields are populated only while a put or "
        "early-redemption process is publicly announced for that bond — empty on most bonds "
        "most days is expected, not missing data.",
        row_key="rows",
        row_mapper=_map_cb_daily_overview,
        whole_market_ok=True,
    ),
    Capability.CB_MONTHLY_ANALYSIS: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondMonthlyAnalysis",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier convertible-bond monthly custody-balance analysis row; "
        "`symbol` is the `cb_id`. Updates Mon-Sat ~18:00; data range starts 2026-05-01.",
        row_key="rows",
        row_mapper=_map_cb_monthly_analysis,
    ),
    Capability.CB_PUT_PROVISION: _DatasetConfig(
        dataset="TaiwanStockConvertibleBondPutProvision",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier convertible-bond put(賣回權) schedule row, including "
        "announced future put dates; `symbol` is the `cb_id`. `data_id` is optional — the date "
        "range alone returns every bond's put schedule. Updates Mon-Fri ~19:00.",
        row_key="rows",
        row_mapper=_map_cb_put_provision,
        whole_market_ok=True,
    ),
}

__all__ = ["DATASETS"]
