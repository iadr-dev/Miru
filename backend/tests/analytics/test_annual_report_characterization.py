"""Characterization of the three large report builders: every branch pinned on synthetic fixtures
so the functions can be split into helpers without changing a single number.

The expected values are the output of the code as it stood before the split."""

from __future__ import annotations

from datetime import date

import pytest

from analytics.annual_report import (
    build_etf_annual_rows,
    build_tw_annual_rows,
    build_us_annual_rows,
)
from schemas.capabilities import DividendEvent, FinancialStatementLine
from schemas.enums import Market
from tests.analytics.test_annual_report import _line, _q, _tw_balance, _tw_income, _us

type Dump = list[dict[str, object]]


def _dump(rows: list[object]) -> Dump:
    return [row.model_dump() for row in rows]  # type: ignore[attr-defined]


def _tw_rich() -> list[object]:
    income = (
        _tw_income(2022, [100, 110, 120, 130])
        + _tw_income(2023, [100, 100, 100, 100])
        + _tw_income(2024, [120, 130, 140])
        + _tw_income(2025, [10])
    )
    income.append(_line(date(2024, 5, 15), "Revenue", 9999))  # not a quarter end: ignored
    balance = (
        _tw_balance(2022, equity=900)
        + _tw_balance(2023, equity=1000)
        + [
            _line(_q(2024, 2), "EquityAttributableToOwnersOfParent", 1300),
            _line(_q(2024, 2), "Liabilities", 500),
            _line(_q(2024, 2), "TotalAssets", 0),  # zero denominator
        ]
    )
    cash = [
        _line(_q(2023, 4), "NetCashInflowFromOperatingActivities", 50),
        _line(_q(2023, 4), "PropertyAndPlantAndEquipment", -20),
        _line(_q(2024, 2), "CashFlowsFromOperatingActivities", 70),  # no capex -> no FCF
    ]
    return list(build_tw_annual_rows(income, balance, cash, years=10))


def _is_q3_revenue(line: FinancialStatementLine) -> bool:
    return line.item == "Revenue" and line.period_end == _q(2023, 3)


def _tw_sparse() -> list[object]:
    income = [line for line in _tw_income(2023, [100, 100, 100, 100]) if not _is_q3_revenue(line)]
    income += _tw_income(2024, [100, 100, 100, 100])
    income += _tw_income(2026, [50, 50])  # 2025 absent: no prior-year bucket
    balance = [_line(_q(2024, 4), "EquityAttributableToOwnersOfParent", 800)]  # no prior equity
    return list(build_tw_annual_rows(income, balance, [], years=10))


def _tw_limited() -> list[object]:
    income = [line for y in (2021, 2022, 2023) for line in _tw_income(y, [10, 10, 10, 10])]
    return list(build_tw_annual_rows(income, [], [], years=2))


def _us_rich() -> list[object]:
    y = [date(2021, 9, 30), date(2022, 9, 30), date(2023, 9, 30), date(2024, 9, 30)]
    income = [
        _us(y[0], "Operating Revenue", 800, "annual"),  # fallback revenue name
        _us(y[0], "Net Income", 80, "annual"),  # fallback net income name
        _us(y[0], "Basic EPS", 1.0, "annual"),
        _us(y[1], "Total Revenue", 1000, "annual"),
        _us(y[1], "Gross Profit", 400, "annual"),
        _us(y[1], "Net Income Common Stockholders", 100, "annual"),
        _us(y[2], "Total Revenue", 0, "annual"),  # zero revenue: margins None, next YoY None
        _us(y[2], "Operating Income", 5, "annual"),
        _us(y[3], "Total Revenue", 500, "annual"),
        _us(y[3], "Operating Income", 50, "annual"),
        _us(y[3], "Net Income Common Stockholders", 60, "annual"),
        _us(y[3], "Diluted EPS", 2.5, "annual"),
    ]
    balance = [
        _us(y[0], "Common Stock Equity", 300, "annual"),  # fallback equity name
        _us(y[1], "Stockholders Equity", 500, "annual"),
        _us(y[1], "Total Liabilities", 700, "annual"),  # fallback liabilities name
        _us(y[1], "Total Assets", 1400, "annual"),
        # y[2]: no balance frame at all -> equity gap
        _us(y[3], "Stockholders Equity", 600, "annual"),
        _us(y[3], "Current Assets", 90, "annual"),
        _us(y[3], "Current Liabilities", 0, "annual"),  # zero denominator
    ]
    cash = [
        _us(y[0], "Cash Flow From Continuing Operating Activities", 90, "annual"),
        _us(y[0], "Capital Expenditure", -10, "annual"),  # derived FCF 80
        _us(y[1], "Operating Cash Flow", 120, "annual"),  # capex absent -> no FCF
        _us(y[3], "Operating Cash Flow", 70, "annual"),
        _us(y[3], "Capital Expenditure", -20, "annual"),
        _us(y[3], "Free Cash Flow", 55, "annual"),  # reported wins
    ]
    return list(build_us_annual_rows(income, balance, cash, years=3))


def _us_empty() -> list[object]:
    return list(build_us_annual_rows([], [], [], years=3))


def _ev(ex: date | None, amount: float | None, announced: date | None = None) -> DividendEvent:
    day = announced or ex or date(2020, 1, 1)
    return DividendEvent(
        symbol="E",
        market=Market.TW,
        announced_date=day,
        fiscal_year="x",
        cash_dividend=amount,
        cash_ex_dividend_date=ex,
    )


def _etf_split() -> list[object]:
    raw = [
        (date(2022, 1, 5), 100.0),
        (date(2022, 12, 30), 110.0),
        (date(2023, 6, 1), 120.0),
        (date(2023, 12, 29), 40.0),  # 3-for-1 split
        (date(2024, 12, 31), 44.0),
        (date(2025, 3, 3), 0.0),  # zero raw price: factor falls back to 1
    ]
    adjusted = [
        (date(2022, 1, 5), 33.3333),
        (date(2022, 12, 30), 36.6667),
        (date(2023, 6, 1), 40.0),
        (date(2023, 12, 29), 40.0),
        (date(2024, 12, 31), 44.0),
        (date(2025, 3, 3), 45.0),
    ]
    events = [
        _ev(date(2022, 6, 1), 3.0),
        _ev(date(2021, 12, 1), 1.0),  # before the first bar: factor 1.0
        _ev(date(2023, 6, 1), 6.0),
        _ev(date(2023, 9, 1), 0.0),  # zero: skipped
        _ev(date(2023, 9, 2), None),  # none: skipped
        _ev(None, 2.0, announced=date(2024, 5, 5)),  # no ex date -> announced year
        _ev(date(2025, 3, 3), 1.0),  # raw price 0 at that day
    ]
    return list(build_etf_annual_rows(raw, adjusted, events, years=10, today=date(2025, 6, 1)))


def _etf_partial_start() -> list[object]:
    closes = [(date(2024, 3, 1), 10.0), (date(2024, 12, 31), 12.0), (date(2025, 12, 31), 15.0)]
    events = [_ev(date(2025, 8, 1), 0.5)]
    return list(build_etf_annual_rows(closes, closes, events, years=10, today=date(2026, 1, 5)))


def _etf_january_start() -> list[object]:
    closes = [(date(2024, 1, 2), 10.0), (date(2024, 12, 31), 12.0), (date(2025, 5, 5), 9.0)]
    return list(build_etf_annual_rows(closes, closes, [], years=1, today=date(2025, 6, 1)))


def _etf_zero_start() -> list[object]:
    closes = [(date(2023, 12, 29), 0.0), (date(2024, 12, 31), 5.0)]
    events = [_ev(date(2024, 5, 1), 1.0)]
    return list(build_etf_annual_rows(closes, closes, events, years=5, today=date(2026, 1, 1)))


def _etf_unsorted_input() -> list[object]:
    adjusted = [(date(2024, 12, 31), 12.0), (date(2023, 12, 29), 10.0)]
    raw = [(date(2023, 12, 29), 10.0), (date(2024, 12, 31), 12.0)]
    events = [_ev(date(2024, 2, 2), 0.6)]
    return list(build_etf_annual_rows(raw, adjusted, events, years=5, today=date(2026, 1, 1)))


GOLDEN: dict[str, Dump] = {
    "etf_january_start": [
        {
            "cash_dividend_adjusted": None,
            "cash_dividend_per_share": None,
            "complete": False,
            "dividend_yield_pct": None,
            "label": "2025（年初至今）",
            "price_return_pct": -25.0,
            "start_close": 12.0,
            "total_return_simple_pct": None,
            "year": 2025,
            "year_end_close": 9.0,
        }
    ],
    "etf_partial_start": [
        {
            "cash_dividend_adjusted": None,
            "cash_dividend_per_share": None,
            "complete": False,
            "dividend_yield_pct": None,
            "label": "2024（部分年度）",
            "price_return_pct": 20.0,
            "start_close": 10.0,
            "total_return_simple_pct": None,
            "year": 2024,
            "year_end_close": 12.0,
        },
        {
            "cash_dividend_adjusted": 0.5,
            "cash_dividend_per_share": 0.5,
            "complete": True,
            "dividend_yield_pct": 3.33,
            "label": "2025",
            "price_return_pct": 25.0,
            "start_close": 12.0,
            "total_return_simple_pct": 29.17,
            "year": 2025,
            "year_end_close": 15.0,
        },
    ],
    "etf_split": [
        {
            "cash_dividend_adjusted": 1.0,
            "cash_dividend_per_share": 3.0,
            "complete": True,
            "dividend_yield_pct": 2.73,
            "label": "2022",
            "price_return_pct": 10.0,
            "start_close": 33.33,
            "total_return_simple_pct": 13.0,
            "year": 2022,
            "year_end_close": 36.67,
        },
        {
            "cash_dividend_adjusted": 2.0,
            "cash_dividend_per_share": 6.0,
            "complete": True,
            "dividend_yield_pct": 5.0,
            "label": "2023",
            "price_return_pct": 9.09,
            "start_close": 36.67,
            "total_return_simple_pct": 14.55,
            "year": 2023,
            "year_end_close": 40.0,
        },
        {
            "cash_dividend_adjusted": 2.0,
            "cash_dividend_per_share": 2.0,
            "complete": True,
            "dividend_yield_pct": 4.55,
            "label": "2024",
            "price_return_pct": 10.0,
            "start_close": 40.0,
            "total_return_simple_pct": 15.0,
            "year": 2024,
            "year_end_close": 44.0,
        },
        {
            "cash_dividend_adjusted": 1.0,
            "cash_dividend_per_share": 1.0,
            "complete": False,
            "dividend_yield_pct": None,
            "label": "2025（年初至今）",
            "price_return_pct": 2.27,
            "start_close": 44.0,
            "total_return_simple_pct": None,
            "year": 2025,
            "year_end_close": 45.0,
        },
    ],
    "etf_unsorted_input": [
        {
            "cash_dividend_adjusted": None,
            "cash_dividend_per_share": None,
            "complete": False,
            "dividend_yield_pct": None,
            "label": "2023（部分年度）",
            "price_return_pct": 0.0,
            "start_close": 10.0,
            "total_return_simple_pct": None,
            "year": 2023,
            "year_end_close": 10.0,
        },
        {
            "cash_dividend_adjusted": 0.6,
            "cash_dividend_per_share": 0.6,
            "complete": True,
            "dividend_yield_pct": 5.0,
            "label": "2024",
            "price_return_pct": 20.0,
            "start_close": 10.0,
            "total_return_simple_pct": 26.0,
            "year": 2024,
            "year_end_close": 12.0,
        },
    ],
    "etf_zero_start": [
        {
            "cash_dividend_adjusted": None,
            "cash_dividend_per_share": None,
            "complete": False,
            "dividend_yield_pct": None,
            "label": "2023（部分年度）",
            "price_return_pct": None,
            "start_close": 0.0,
            "total_return_simple_pct": None,
            "year": 2023,
            "year_end_close": 0.0,
        },
        {
            "cash_dividend_adjusted": 1.0,
            "cash_dividend_per_share": 1.0,
            "complete": True,
            "dividend_yield_pct": 20.0,
            "label": "2024",
            "price_return_pct": None,
            "start_close": 0.0,
            "total_return_simple_pct": 0.0,
            "year": 2024,
            "year_end_close": 5.0,
        },
    ],
    "tw_limited": [
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2022",
            "net_income": 10.0,
            "operating_margin_pct": 30.0,
            "quarters": 4,
            "revenue": 40.0,
            "revenue_yoy_pct": 0.0,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2022,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2023",
            "net_income": 10.0,
            "operating_margin_pct": 30.0,
            "quarters": 4,
            "revenue": 40.0,
            "revenue_yoy_pct": 0.0,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2023,
            "year_end_close": None,
        },
    ],
    "tw_rich": [
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": 2.0,
            "debt_ratio_pct": 40.0,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2022",
            "net_income": 115.0,
            "operating_margin_pct": 30.0,
            "quarters": 4,
            "revenue": 460.0,
            "revenue_yoy_pct": None,
            "roe_pct": 12.777777777777777,
            "stock_dividend": None,
            "year": 2022,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": 2.0,
            "debt_ratio_pct": 40.0,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": 30.0,
            "gross_margin_pct": 50.0,
            "label": "2023",
            "net_income": 100.0,
            "operating_margin_pct": 30.0,
            "quarters": 4,
            "revenue": 400.0,
            "revenue_yoy_pct": -13.043478260869565,
            "roe_pct": 10.526315789473683,
            "stock_dividend": None,
            "year": 2023,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": False,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 6.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2024 前3季",
            "net_income": 97.5,
            "operating_margin_pct": 30.0,
            "quarters": 3,
            "revenue": 390.0,
            "revenue_yoy_pct": 30.0,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2024,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": False,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 2.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2025 前1季",
            "net_income": 2.5,
            "operating_margin_pct": 30.0,
            "quarters": 1,
            "revenue": 10.0,
            "revenue_yoy_pct": -91.66666666666666,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2025,
            "year_end_close": None,
        },
    ],
    "tw_sparse": [
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": None,
            "gross_margin_pct": 66.66666666666666,
            "label": "2023",
            "net_income": 100.0,
            "operating_margin_pct": 40.0,
            "quarters": 4,
            "revenue": 300.0,
            "revenue_yoy_pct": None,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2023,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 8.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2024",
            "net_income": 100.0,
            "operating_margin_pct": 30.0,
            "quarters": 4,
            "revenue": 400.0,
            "revenue_yoy_pct": None,
            "roe_pct": 12.5,
            "stock_dividend": None,
            "year": 2024,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": False,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 4.0,
            "free_cash_flow": None,
            "gross_margin_pct": 50.0,
            "label": "2026 前2季",
            "net_income": 25.0,
            "operating_margin_pct": 30.0,
            "quarters": 2,
            "revenue": 100.0,
            "revenue_yoy_pct": None,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2026,
            "year_end_close": None,
        },
    ],
    "us_empty": [],
    "us_rich": [
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": 50.0,
            "dividend_yield_pct": None,
            "eps": None,
            "free_cash_flow": None,
            "gross_margin_pct": 40.0,
            "label": "2022",
            "net_income": 100.0,
            "operating_margin_pct": None,
            "quarters": None,
            "revenue": 1000.0,
            "revenue_yoy_pct": 25.0,
            "roe_pct": 25.0,
            "stock_dividend": None,
            "year": 2022,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": None,
            "free_cash_flow": None,
            "gross_margin_pct": None,
            "label": "2023",
            "net_income": None,
            "operating_margin_pct": None,
            "quarters": None,
            "revenue": 0.0,
            "revenue_yoy_pct": -100.0,
            "roe_pct": None,
            "stock_dividend": None,
            "year": 2023,
            "year_end_close": None,
        },
        {
            "cash_dividend": None,
            "complete": True,
            "current_ratio": None,
            "debt_ratio_pct": None,
            "dividend_yield_pct": None,
            "eps": 2.5,
            "free_cash_flow": 55.0,
            "gross_margin_pct": None,
            "label": "2024",
            "net_income": 60.0,
            "operating_margin_pct": 10.0,
            "quarters": None,
            "revenue": 500.0,
            "revenue_yoy_pct": None,
            "roe_pct": 10.0,
            "stock_dividend": None,
            "year": 2024,
            "year_end_close": None,
        },
    ],
}

SCENARIOS = {
    "tw_rich": _tw_rich,
    "tw_sparse": _tw_sparse,
    "tw_limited": _tw_limited,
    "us_rich": _us_rich,
    "us_empty": _us_empty,
    "etf_split": _etf_split,
    "etf_partial_start": _etf_partial_start,
    "etf_january_start": _etf_january_start,
    "etf_zero_start": _etf_zero_start,
    "etf_unsorted_input": _etf_unsorted_input,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_report_rows_are_unchanged(name: str) -> None:
    assert _dump(SCENARIOS[name]()) == GOLDEN[name]


def test_tw_empty_inputs_give_no_rows() -> None:
    assert build_tw_annual_rows([], [], [], years=5) == []


def test_etf_empty_adjusted_series_gives_no_rows() -> None:
    raw = [(date(2024, 1, 2), 1.0)]
    assert build_etf_annual_rows(raw, [], [], years=5, today=date(2025, 1, 1)) == []
