"""FinMind Fundamental-category datasets — financial statements (income/balance-sheet/cash-
flow), dividend policy + ex-dividend results, monthly revenue filings, market-value/weight,
and corporate-action reference rows (capital reduction, delisting, split, par-value change).
14 datasets.
"""

from __future__ import annotations

from collections.abc import Callable

from schemas.enums import Capability, DataClass, TimingClass
from schemas.market import Symbol

from ._shared import _as_float, _as_int, _DatasetConfig, _row_symbol


def _make_financial_statement_mapper(
    statement_type: str,
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`TaiwanStockFinancialStatements`/`TaiwanStockBalanceSheet`/`TaiwanStockCashFlowsStatement`
    share an identical `date/stock_id/type/value/origin_name` shape — one parameterized mapper
    for all three, distinguished only by the normalized `statement_type` label."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        lines: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_symbol(row, sym)
            lines.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "period_end": row["date"],
                    "statement_type": statement_type,
                    "item": str(row.get("type", "")),
                    "origin_name": str(row.get("origin_name", "")),
                    "value": _as_float(row.get("value")),
                }
            )
        return lines

    return _mapper


_map_financials = _make_financial_statement_mapper("income_statement")
_map_balance_sheet = _make_financial_statement_mapper("balance_sheet")
_map_cash_flow_statement = _make_financial_statement_mapper("cash_flow")


def _map_dividends(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        events.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "announced_date": row.get("AnnouncementDate") or row["date"],
                "fiscal_year": str(row.get("year", "")),
                "cash_dividend": row.get("CashEarningsDistribution"),
                "stock_dividend": row.get("StockEarningsDistribution"),
                "cash_ex_dividend_date": row.get("CashExDividendTradingDate") or None,
                "stock_ex_dividend_date": row.get("StockExDividendTradingDate") or None,
            }
        )
    return events


def _map_dividend_result(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "before_price": _as_float(row.get("before_price")),
                "after_price": _as_float(row.get("after_price")),
                "stock_and_cache_dividend": _as_float(row.get("stock_and_cache_dividend")),
                "stock_or_cache_dividend": str(row.get("stock_or_cache_dividend", "")),
                "max_price": _as_float(row.get("max_price")),
                "min_price": _as_float(row.get("min_price")),
                "open_price": _as_float(row.get("open_price")),
                "reference_price": _as_float(row.get("reference_price")),
            }
        )
    return rows_out


def _map_month_revenue(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "country": str(row.get("country", "")),
                "revenue": _as_int(row.get("revenue")),
                "revenue_month": _as_int(row.get("revenue_month")),
                "revenue_year": _as_int(row.get("revenue_year")),
                "create_time": row.get("create_time") or None,
            }
        )
    return rows_out


def _map_capital_reduction_reference_price(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "closing_price_on_last_trading_day": _as_float(
                    row.get("ClosingPriceonTheLastTradingDay")
                ),
                "post_reduction_reference_price": _as_float(row.get("PostReductionReferencePrice")),
                "limit_up": _as_float(row.get("LimitUp")),
                "limit_down": _as_float(row.get("LimitDown")),
                "opening_reference_price": _as_float(row.get("OpeningReferencePrice")),
                "exright_reference_price": _as_float(row.get("ExrightReferencePrice")),
                "reason_for_capital_reduction": str(row.get("ReasonforCapitalReduction", "")),
            }
        )
    return rows_out


def _map_market_value(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanStockMarketValue` — Backer/Sponsor-only, no free per-symbol tier."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "market_value": _as_int(row.get("market_value")),
            }
        )
    return rows_out


def _map_stock_delisting(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockDelisting` — reference table, no `data_id` param exists."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, None)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "stock_name": str(row.get("stock_name", "")),
            }
        )
    return rows_out


def _map_market_value_weight(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockMarketValueWeight` — Backer/Sponsor-only, no free per-symbol tier."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "rank": _as_int(row.get("rank")),
                "stock_name": str(row.get("stock_name", "")),
                "weight_per": _as_float(row.get("weight_per")),
                "listing_type": str(row.get("type", "")),
            }
        )
    return rows_out


def _map_split_price(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "split_type": str(row.get("type", "")),
                "before_price": _as_float(row.get("before_price")),
                "after_price": _as_float(row.get("after_price")),
                "max_price": _as_float(row.get("max_price")),
                "min_price": _as_float(row.get("min_price")),
                "open_price": _as_float(row.get("open_price")),
            }
        )
    return rows_out


def _map_par_value_change(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "stock_name": str(row.get("stock_name", "")),
                "before_close": _as_float(row.get("before_close")),
                "after_ref_close": _as_float(row.get("after_ref_close")),
                "after_ref_max": _as_float(row.get("after_ref_max")),
                "after_ref_min": _as_float(row.get("after_ref_min")),
                "after_ref_open": _as_float(row.get("after_ref_open")),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.FINANCIALS: _DatasetConfig(
        dataset="TaiwanStockFinancialStatements",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Aggregated financial-statement line items; reporting-period lag applies, "
        "not intraday.",
        row_key="lines",
        row_mapper=_map_financials,
        # The upstream catalog documents an omit-data_id whole-table form for this dataset
        # identical in shape to the generic /data endpoint's other whole-market datasets
        # (same "start_date only" request, same envelope). Filings themselves land on
        # irregular per-company dates driven by statutory reporting deadlines, not a fixed
        # daily publish time — this only says the *fetch* mechanics are whole-market-capable,
        # not that a caller should expect a fresh row for every stock every day.
        whole_market_ok=True,
    ),
    Capability.DIVIDENDS: _DatasetConfig(
        dataset="TaiwanStockDividend",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Dividend-policy announcements; reflects the announced schedule, not a live feed.",
        row_key="events",
        row_mapper=_map_dividends,
        # Same documented omit-data_id whole-table form as FINANCIALS above; dividend-policy
        # announcements are board-resolution-driven (event timing, not a daily market close),
        # so this dataset is fetch-mechanically whole-market-capable without being a
        # daily-clock-time EOD dataset.
        whole_market_ok=True,
    ),
    Capability.BALANCE_SHEET: _DatasetConfig(
        dataset="TaiwanStockBalanceSheet",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Balance-sheet line items; reporting-period lag applies, not intraday.",
        row_key="lines",
        row_mapper=_map_balance_sheet,
        whole_market_ok=True,
    ),
    Capability.CASH_FLOW_STATEMENT: _DatasetConfig(
        dataset="TaiwanStockCashFlowsStatement",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Cash-flow-statement line items; reporting-period lag applies, not intraday.",
        row_key="lines",
        row_mapper=_map_cash_flow_statement,
        whole_market_ok=True,
    ),
    Capability.DIVIDEND_RESULT: _DatasetConfig(
        dataset="TaiwanStockDividendResult",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Ex-dividend/ex-rights result row; reflects the announced/settled event, not a "
        "daily-clock-time market snapshot.",
        row_key="rows",
        row_mapper=_map_dividend_result,
        whole_market_ok=True,
    ),
    Capability.MONTH_REVENUE: _DatasetConfig(
        dataset="TaiwanStockMonthRevenue",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Monthly revenue filing; published on the issuer's statutory reporting cadence, "
        "not a daily-clock-time market snapshot.",
        row_key="rows",
        row_mapper=_map_month_revenue,
        whole_market_ok=True,
    ),
    Capability.CAPITAL_REDUCTION_REFERENCE_PRICE: _DatasetConfig(
        dataset="TaiwanStockCapitalReductionReferencePrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Post-capital-reduction reference-price row; event-driven. `data_id` is "
        "documented as optional, but the omit-`data_id` whole-range form requires "
        "Backer/Sponsor — free-tier callers must always pass `data_id`, so this dataset is "
        "not marked whole_market_ok for the free per-symbol path.",
        row_key="rows",
        row_mapper=_map_capital_reduction_reference_price,
    ),
    Capability.MARKET_VALUE: _DatasetConfig(
        dataset="TaiwanStockMarketValue",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-only market-capitalization row; no free per-symbol tier exists "
        "for this dataset. Updates Mon-Fri ~23:30.",
        row_key="rows",
        row_mapper=_map_market_value,
        whole_market_ok=True,
    ),
    Capability.STOCK_DELISTING: _DatasetConfig(
        dataset="TaiwanStockDelisting",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Whole-market delisting-event reference table; no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_stock_delisting,
        whole_market_ok=True,
    ),
    Capability.MARKET_VALUE_WEIGHT: _DatasetConfig(
        dataset="TaiwanStockMarketValueWeight",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-only market-cap-weight ranking row; no free per-symbol tier "
        "exists for this dataset.",
        row_key="rows",
        row_mapper=_map_market_value_weight,
        whole_market_ok=True,
    ),
    Capability.SPLIT_PRICE: _DatasetConfig(
        dataset="TaiwanStockSplitPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Stock-split/reverse-split reference-price row; event-driven, no `data_id` "
        "param exists.",
        row_key="rows",
        row_mapper=_map_split_price,
        whole_market_ok=True,
    ),
    Capability.PAR_VALUE_CHANGE: _DatasetConfig(
        dataset="TaiwanStockParValueChange",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Par-value-change reference-price row; event-driven, no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_par_value_change,
        whole_market_ok=True,
    ),
}

__all__ = ["DATASETS"]
