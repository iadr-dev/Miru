"""Convertible bond rows."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


class ConvertibleBondInfoRow(BaseModel):
    """`cb_info` — one 可轉債 (convertible bond) roster row. `symbol` is the bond's `cb_id` —
    a convertible-bond identifier, distinct from an equity `stock_id`, never conflated
    downstream. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    initial_date_of_conversion: date_ | None = None
    due_date_of_conversion: date_ | None = None
    issuance_amount: float | None = None


class ConvertibleBondDailyRow(BaseModel):
    """`cb_daily` — one convertible bond's EOD trading row. `symbol` is the `cb_id`, not an
    equity `stock_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    transaction_type: str
    close: float
    change: float
    open: float
    high: float
    low: float
    number_of_transactions: int
    unit: str
    trading_value: float
    avg_price: float
    next_ref_price: float
    next_max_limit: float
    next_min_limit: float


class ConvertibleBondInstitutionalFlowRow(BaseModel):
    """`cb_institutional_flow` — one convertible bond's three-major-institutional-investor
    daily buy/sell/overbuy row. `symbol` is the `cb_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    foreign_investor_buy: float
    foreign_investor_sell: float
    foreign_investor_overbuy: float
    investment_trust_buy: float
    investment_trust_sell: float
    investment_trust_overbuy: float
    dealer_self_buy: float
    dealer_self_sell: float
    dealer_self_overbuy: float
    total_overbuy: float


class ConvertibleBondDailyOverviewRow(BaseModel):
    """`cb_daily_overview` — one convertible bond's daily overview row, including conversion/
    put/early-redemption terms. `symbol` is the `cb_id`. Backer/Sponsor-only. The put/early-
    redemption fields (`latest_initial_date_of_put` through `early_redemption_price`) are
    populated only while a put or early-redemption process is publicly announced for that
    bond — empty on most bonds most days is expected, not missing data; this dataset does not
    carry the bond's full static issuance terms (e.g. a complete put schedule)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    initial_date_of_conversion: date_ | None = None
    due_date_of_conversion: date_ | None = None
    initial_date_of_stop_conversion: date_ | None = None
    due_date_of_stop_conversion: date_ | None = None
    conversion_price: float | None = None
    next_effective_date_of_conversion_price: date_ | None = None
    latest_initial_date_of_put: date_ | None = None
    latest_due_date_of_put: date_ | None = None
    latest_put_price: float | None = None
    initial_date_of_early_redemption: date_ | None = None
    due_date_of_early_redemption: date_ | None = None
    early_redemption_price: float | None = None
    date_of_delisted: date_ | None = None
    issuance_amount: float | None = None
    outstanding_amount: float | None = None
    reference_price: float | None = None
    price_of_underlying_stock: float | None = None
    initial_date_of_suspension: date_ | None = None
    due_date_of_suspension: date_ | None = None
    coupon_rate: float | None = None


class ConvertibleBondMonthlyAnalysisRow(BaseModel):
    """`cb_monthly_analysis` — one convertible bond's monthly custody-balance analysis row.
    `symbol` is the `cb_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    name_en: str
    trade_date: date_
    custody_balance: int
    last_month_balance: int
    change: int
    change_percent: float
    issued_units: int
    custody_accounts: int
    pledged_units: int


class ConvertibleBondPutProvisionRow(BaseModel):
    """`cb_put_provision` — one convertible bond's 賣回權 (put) schedule row (`date` is the put
    record date; announced future put dates typically extend ~1 year ahead). `symbol` is the
    `cb_id`. Backer/Sponsor-only; complements `cb_daily_overview`'s put fields, which are only
    populated during announced put windows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    date: date_
    name: str  # cb_name
    put_price: float
    put_yield_rate: float
