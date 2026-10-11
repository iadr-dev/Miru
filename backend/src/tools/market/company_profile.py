"""The company profile card (`get_company_profile`)."""

from __future__ import annotations

import asyncio
from datetime import date as date_
from typing import cast

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field

from analytics.fundamentals import (
    compute_fundamental_metrics,
    compute_revenue_yoy,
    load_symbol_financials,
    load_symbol_market_value,
    load_symbol_month_revenue,
)
from dataaccess.router import DataAccessRouter
from ingest.store import ParquetStore
from schemas.capabilities import (
    DividendEvent,
    MarketValueRow,
    SectorClassification,
    ValuationRatiosRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import validate_rows
from tools.base import BaseTool, ToolContext, parse_args
from tools.common import SymbolInput
from tools.market._common import RowList, UntypedDict, real_calendar

type _OptionalStr = str | None


# TW's `financials` capability (FinMind-sourced) reports the line item as plain `"EPS"`;
# yfinance's own income-statement frame (every other market) reports `"Diluted EPS"`/
# `"Basic EPS"` instead — no single name works for every source, so `_latest_eps` matches
# either, preferring Diluted (the more conservative, dilution-adjusted figure) when both exist
# for the same period.
_EPS_ITEM_NAMES: tuple[str, ...] = ("EPS", "Diluted EPS", "Basic EPS")


def _latest_eps(payload: dict[str, object]) -> float | None:
    """`financials`' payload is `{"lines": [...]}` — one row per (period, statement line);
    the most recent EPS-item row's `value` is the latest reported EPS (see `_EPS_ITEM_NAMES`)."""
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        return None
    rows = cast(RowList, raw_lines)
    eps_rows = [row for row in rows if row.get("item") in _EPS_ITEM_NAMES]
    if not eps_rows:
        return None
    latest_period = max(cast("str", row.get("period_end", "")) for row in eps_rows)
    same_period = [row for row in eps_rows if row.get("period_end") == latest_period]
    for name in _EPS_ITEM_NAMES:
        match = next((row for row in same_period if row.get("item") == name), None)
        if match is not None:
            value = match.get("value")
            return float(cast("float", value)) if isinstance(value, int | float) else None
    return None


# yfinance's own income-statement item names for revenue/gross-profit (every non-TW market) —
# distinct from `analytics/fundamentals.py`'s FinMind-shaped `_REVENUE_ITEMS`/
# `_GROSS_PROFIT_ITEMS` constants, which name TW's own line-item vocabulary instead.
_YF_REVENUE_ITEM_NAMES: tuple[str, ...] = ("Total Revenue", "Operating Revenue")


_YF_GROSS_PROFIT_ITEM_NAME = "Gross Profit"


_MIN_PERIODS_FOR_YOY = 2


def _yfinance_revenue_yoy_and_margins(
    payload: dict[str, object],
) -> tuple[float | None, float | None]:
    """`margins`/`revenue_yoy` for every non-TW market, computed live from the SAME `financials`
    payload `_latest_eps` already reads — no derived-store dependency (unlike TW, no US/GB/JP/EU
    ingest job populates `financials_lines`/`month_revenue_rows` at all, so the store-backed path
    `_revenue_yoy_and_margins` uses would stay permanently empty for these markets). Only the
    ANNUAL period rows are used for both: yfinance's own annual revenue series is the natural
    "vs a year ago" comparison (unlike TW's month_revenue feed, yfinance has no monthly revenue
    series at all), and gross margin is computed off the same annual Gross Profit/Total Revenue
    pair rather than mixing annual and quarterly periods."""
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        return None, None
    rows = cast(RowList, raw_lines)
    annual_rows = [row for row in rows if row.get("period_type") == "annual"]

    def _annual_value(names: tuple[str, ...]) -> dict[str, float]:
        by_period: dict[str, float] = {}
        for row in annual_rows:
            item = row.get("item")
            period_end = row.get("period_end")
            value = row.get("value")
            if item in names and isinstance(period_end, str) and isinstance(value, int | float):
                by_period[period_end] = float(value)
        return by_period

    revenue_by_period = _annual_value(_YF_REVENUE_ITEM_NAMES)
    gross_profit_by_period = _annual_value((_YF_GROSS_PROFIT_ITEM_NAME,))

    # A plain ratio (e.g. `0.18` for +18%), matching `analytics/fundamentals.py`'s own
    # `compute_revenue_yoy`/`_safe_ratio` scale for the TW path this field shares a name with —
    # never re-scaled here, so the two markets' values stay directly comparable under one field.
    revenue_yoy: float | None = None
    periods = sorted(revenue_by_period, reverse=True)
    if len(periods) >= _MIN_PERIODS_FOR_YOY:
        latest_revenue = revenue_by_period[periods[0]]
        prior_revenue = revenue_by_period[periods[1]]
        if prior_revenue != 0:
            revenue_yoy = latest_revenue / prior_revenue - 1

    margins: float | None = None
    if periods:
        latest_period = periods[0]
        margin_revenue = revenue_by_period.get(latest_period)
        margin_gross_profit = gross_profit_by_period.get(latest_period)
        if margin_revenue is not None and margin_gross_profit is not None and margin_revenue != 0:
            margins = margin_gross_profit / margin_revenue

    return revenue_yoy, margins


# MOPS-sourced identity fields (`Capability.PROFILE`'s `profile` payload) that map straight into
# `GetCompanyProfileOutput` — named once here so the enrichment loop and `missing_fields` fallback
# both walk the exact same set instead of two hand-kept lists drifting apart.
_MOPS_PROFILE_FIELDS: tuple[str, ...] = (
    "chairman",
    "auditor",
    "headquarters",
    "website",
    "capital",
    "spokesperson",
    "founding_date",
    "industry_classification_code",
)


# `revenue_yoy`/`margins` are read back from the derived store (whole-market-ingested
# `TaiwanStockFinancialStatements`/`TaiwanStockMonthRevenue`, same pattern `get_etf_money_flow`
# uses for its own cross-symbol read) rather than fetched live through the router — see
# `analytics/fundamentals.py`'s module docstring. Lookback windows are trading days ending today:
# 400 comfortably spans several quarterly filing cycles (mirrors `scoring_batch.py`'s own
# `_FINANCIALS_LOOKBACK_DAYS`); revenue filings land monthly, so 400 trading days (~19 months)
# reliably covers the same calendar month one year prior even with a late filing.
_FINANCIALS_STORE_LOOKBACK_DAYS = 400


_MONTH_REVENUE_STORE_LOOKBACK_DAYS = 400


class GetCompanyProfileOutput(BaseModel):
    """A company-profile card composed from every capability wired below: `sector_class`
    (industry/sector), `dividends` (latest announced dividend), `market_value`/
    `valuation_ratios` (市值/PER/殖利率), `financials` (EPS, from the income-statement lines),
    `profile` (MOPS-sourced identity fields — 董事長/簽證會計師/住址/官網/資本額/發言人/成立日期/
    產業別), and two derived-store reads: `revenue_yoy` (this month's filed revenue vs the same
    calendar month a year prior, from the ingested `TaiwanStockMonthRevenue` dataset) and
    `margins` (TTM gross profit ÷ TTM revenue, from the same ingested statement lines `eps`
    reads — see `analytics/fundamentals.py`). Each field is only ever populated from a genuinely
    healthy fetch/read for this call; `missing_fields` is computed per-call from which fetches
    actually succeeded, never a static reduced list, so the agent never claims completeness it
    doesn't have."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    sector: str | None = None
    industry: str | None = None
    latest_dividend: DividendEvent | None = None
    market_value: int | None = None
    per: float | None = None
    dividend_yield: float | None = None
    eps: float | None = None
    chairman: str | None = None
    auditor: str | None = None
    headquarters: str | None = None
    website: str | None = None
    capital: int | None = None
    spokesperson: str | None = None
    founding_date: str | None = None
    industry_classification_code: str | None = None
    revenue_yoy: float | None = None
    margins: float | None = None
    sector_caveat: str = Field(min_length=1)
    dividend_caveat: str = Field(min_length=1)
    missing_fields: tuple[str, ...] = Field(
        default=(),
        description="Fields this profile call could not populate — either no adapter exists "
        "for that capability, or this call's fetch of it was unhealthy/empty. Never silently "
        "omitted from the caller.",
    )


class GetCompanyProfileTool(BaseTool):
    """`get_company_profile` — composes `sector_class` + `dividends` + `market_value` +
    `valuation_ratios` + `financials` + `profile` (MOPS) into one card, plus `revenue_yoy`/
    `margins` read back from the derived Parquet store (see `analytics/fundamentals.py`) rather
    than fetched live — same "whole-market dataset, read back not fetched live" pattern
    `get_etf_money_flow` uses. Every field is real when its capability's fetch/read is healthy for
    this call; otherwise it's `None` and named in `missing_fields` rather than faked. Immediate
    core."""

    name = "get_company_profile"
    description = (
        "Get a company-profile card for one stock: sector/industry classification, latest "
        "announced dividend, market value/PER/dividend yield/EPS, revenue YoY, gross margin, and "
        "MOPS-sourced identity fields (chairman/auditor/headquarters/website/capital/"
        "spokesperson/founding date/industry classification code). See `missing_fields` for "
        "anything this call couldn't populate."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetCompanyProfileOutput
    card_type: str | None = "company_profile"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter, store: ParquetStore) -> None:
        self._router = router
        self._store = store

    @staticmethod
    def _sector_and_industry(sector_payload: dict[str, object]) -> tuple[str | None, str | None]:
        classification = sector_payload.get("classification")
        if not isinstance(classification, dict):
            return None, None
        parsed = SectorClassification.model_validate(cast(UntypedDict, classification))
        return parsed.sector, parsed.industry

    @staticmethod
    def _latest_dividend(dividend_payload: dict[str, object]) -> DividendEvent | None:
        raw_events = dividend_payload.get("events")
        if not (isinstance(raw_events, list) and raw_events):
            return None
        rows = cast(RowList, raw_events)
        return DividendEvent.model_validate(rows[-1])

    @staticmethod
    def _mops_fields(profile_payload: dict[str, object]) -> dict[str, object | None]:
        mops_fields: dict[str, object | None] = dict.fromkeys(_MOPS_PROFILE_FIELDS)
        mops_profile = profile_payload.get("profile")
        if isinstance(mops_profile, dict):
            typed_profile = cast(UntypedDict, mops_profile)
            for field_name in _MOPS_PROFILE_FIELDS:
                mops_fields[field_name] = typed_profile.get(field_name)
        return mops_fields

    def _revenue_yoy_and_margins(
        self, *, symbol: Symbol, market: Market
    ) -> tuple[float | None, float | None]:
        """Reads back the two derived-store-backed fields. `financials_lines` (income/balance/
        cash-flow) already backs `margins` via the same TTM gross-profit/revenue lines
        `compute_fundamental_metrics` uses; `month_revenue_rows` backs `revenue_yoy` via
        `compute_revenue_yoy`. Both degrade to `None` independently when their own store read has
        no rows for this symbol yet (not-yet-ingested/no-filing), never a fabricated number."""
        calendar = real_calendar(self._store, market.value)
        today = date_.today()
        financials_window = calendar.business_days_between(
            calendar.shift_business_days(today, -(_FINANCIALS_STORE_LOOKBACK_DAYS - 1)), today
        )
        revenue_window = calendar.business_days_between(
            calendar.shift_business_days(today, -(_MONTH_REVENUE_STORE_LOOKBACK_DAYS - 1)), today
        )
        lines = load_symbol_financials(
            self._store, market=market, run_dates=financials_window, symbol=symbol.id
        )
        revenue_rows = load_symbol_month_revenue(
            self._store, market=market, run_dates=revenue_window, symbol=symbol.id
        )
        market_value = load_symbol_market_value(
            self._store, market=market, run_dates=financials_window, symbol=symbol.id
        )
        margins = compute_fundamental_metrics(lines, market_value).gross_margin
        revenue_yoy = compute_revenue_yoy(revenue_rows)
        return revenue_yoy, margins

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        # Six independent per-capability fetches for the same symbol — none of their requests or
        # post-processing depends on another's result, so they run concurrently rather than one
        # await at a time. Sequential awaits here previously meant this single tool call could
        # take 40+ seconds with zero intermediate `tool.start`/`tool.result` events (each fetch is
        # inside this one dispatch, not its own agent-loop step) — a live process timeline showed
        # a single unchanging "查詢中" status the whole time, indistinguishable from a hang.
        # `router.fetch` never raises for a genuine no-data case (degrades to an `unavailable`
        # `SourceResult`), so no `return_exceptions` handling is needed beyond what sequential
        # awaits already had.
        (
            sector_result,
            dividend_result,
            market_value_result,
            valuation_result,
            financials_result,
            profile_result,
        ) = await asyncio.gather(
            self._router.fetch(Capability.SECTOR_CLASS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.DIVIDENDS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.MARKET_VALUE, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.VALUATION_RATIOS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.FINANCIALS, symbol, tls_policy=ctx.tls_policy),
            self._router.fetch(Capability.PROFILE, symbol, tls_policy=ctx.tls_policy),
        )

        missing_fields: list[str] = []

        sector, industry = self._sector_and_industry(sector_result.payload)
        latest_dividend = self._latest_dividend(dividend_result.payload)

        market_value_rows = validate_rows(MarketValueRow, market_value_result.payload, "rows")
        market_value = market_value_rows[-1].market_value if market_value_rows else None
        if market_value is None:
            missing_fields.append("market_value")

        valuation_rows = validate_rows(ValuationRatiosRow, valuation_result.payload, "rows")
        latest_valuation = valuation_rows[-1] if valuation_rows else None
        per = latest_valuation.per if latest_valuation else None
        dividend_yield = latest_valuation.dividend_yield if latest_valuation else None
        if per is None:
            missing_fields.append("per")
        if dividend_yield is None:
            missing_fields.append("dividend_yield")

        eps = _latest_eps(financials_result.payload)
        if eps is None:
            missing_fields.append("eps")

        # TW reads back the derived-store-ingested feeds (`_revenue_yoy_and_margins`); every
        # other market has no such ingest job at all, so this computes the same two fields live
        # off the `financials` fetch already made above instead of a permanently-empty store read.
        if typed.market is Market.TW:
            # Synchronous Parquet reads + validation: off the event loop.
            revenue_yoy, margins = await anyio.to_thread.run_sync(
                lambda: self._revenue_yoy_and_margins(symbol=symbol, market=typed.market)
            )
        else:
            revenue_yoy, margins = _yfinance_revenue_yoy_and_margins(financials_result.payload)
        if revenue_yoy is None:
            missing_fields.append("revenue_yoy")
        if margins is None:
            missing_fields.append("margins")

        mops_fields = self._mops_fields(profile_result.payload)
        missing_fields.extend(
            field_name for field_name in _MOPS_PROFILE_FIELDS if mops_fields[field_name] is None
        )

        return GetCompanyProfileOutput(
            symbol=symbol.id,
            market=symbol.market.value,
            sector=sector,
            industry=industry,
            latest_dividend=latest_dividend,
            market_value=market_value,
            per=per,
            dividend_yield=dividend_yield,
            eps=eps,
            chairman=cast(_OptionalStr, mops_fields["chairman"]),
            auditor=cast(_OptionalStr, mops_fields["auditor"]),
            headquarters=cast(_OptionalStr, mops_fields["headquarters"]),
            website=cast(_OptionalStr, mops_fields["website"]),
            capital=cast("int | None", mops_fields["capital"]),
            spokesperson=cast(_OptionalStr, mops_fields["spokesperson"]),
            founding_date=cast(_OptionalStr, mops_fields["founding_date"]),
            industry_classification_code=cast(
                "str | None", mops_fields["industry_classification_code"]
            ),
            revenue_yoy=revenue_yoy,
            margins=margins,
            sector_caveat=sector_result.currentness_caveat,
            dividend_caveat=dividend_result.currentness_caveat,
            missing_fields=tuple(missing_fields),
        )
