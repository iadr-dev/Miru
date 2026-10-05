"""The report maths, pinned on hand-checkable numbers. Item names and cumulation rules mirror what
the real FinMind (TW, quarterly, cash flow year-to-date) and yfinance (US, annual) frames carry."""

from __future__ import annotations

from datetime import date

import pytest

from analytics.annual_report import (
    attach_dividends,
    build_etf_annual_rows,
    build_tw_annual_rows,
    build_us_annual_rows,
    monthly_seasonality,
    quarterly_seasonality,
)
from analytics.risk_metrics import adjust_discontinuities
from schemas.capabilities import DividendEvent, EODBar, FinancialStatementLine, MonthRevenueRow
from schemas.enums import Market


def _line(end: date, item: str, value: float, period_type: str = "quarterly", market=Market.TW):
    return FinancialStatementLine(
        symbol="X",
        market=market,
        period_end=end,
        statement_type="income",
        period_type=period_type,
        item=item,
        origin_name=item,
        value=value,
    )


def _q(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 30 if quarter in (2, 3) else 31)


def _tw_income(year: int, revenues: list[float], eps: float = 2.0):
    gross, op, ni = 0.5, 0.3, 0.25
    lines = []
    for quarter, revenue in enumerate(revenues, start=1):
        end = _q(year, quarter)
        lines += [
            _line(end, "Revenue", revenue),
            _line(end, "GrossProfit", revenue * gross),
            _line(end, "OperatingIncome", revenue * op),
            _line(end, "EquityAttributableToOwnersOfParent", revenue * ni),
            _line(end, "EPS", eps),
        ]
    return lines


def _tw_balance(year: int, *, equity: float):
    liabilities, assets, ca, cl = 400.0, 1000.0, 600.0, 300.0
    end = _q(year, 4)
    return [
        _line(end, "EquityAttributableToOwnersOfParent", equity),
        _line(end, "Liabilities", liabilities),
        _line(end, "TotalAssets", assets),
        _line(end, "CurrentAssets", ca),
        _line(end, "CurrentLiabilities", cl),
    ]


def test_tw_year_rolls_up_quarters_and_derives_ratios() -> None:
    income = _tw_income(2023, [100] * 4) + _tw_income(2024, [120] * 4)
    balance = _tw_balance(2023, equity=1000) + _tw_balance(2024, equity=1200)
    cash = [
        _line(_q(2024, 4), "CashFlowsFromOperatingActivities", 300),
        _line(_q(2024, 4), "PropertyAndPlantAndEquipment", -120),
    ]

    rows = build_tw_annual_rows(income, balance, cash, years=5)
    by_year = {row.year: row for row in rows}

    assert by_year[2024].revenue == 480
    assert by_year[2024].revenue_yoy_pct == pytest.approx(20.0)  # 480 vs 400
    assert by_year[2024].gross_margin_pct == pytest.approx(50.0)
    assert by_year[2024].operating_margin_pct == pytest.approx(30.0)
    assert by_year[2024].eps == 8.0  # four quarters of 2.0
    assert by_year[2024].roe_pct == pytest.approx(120 / 1100 * 100)  # NI 120 / avg(1000, 1200)
    assert by_year[2024].debt_ratio_pct == pytest.approx(40.0)
    assert by_year[2024].current_ratio == pytest.approx(2.0)
    assert by_year[2024].free_cash_flow == 180  # OCF 300 less capex 120
    assert by_year[2023].revenue_yoy_pct is None  # no 2022 in the data
    assert by_year[2024].complete is True


def test_tw_partial_year_is_year_to_date_and_never_annualised() -> None:
    income = _tw_income(2024, [100] * 4) + _tw_income(2025, [150, 150])
    balance = _tw_balance(2024, equity=1000)

    rows = build_tw_annual_rows(income, balance, [], years=5)
    partial = next(row for row in rows if row.year == 2025)

    assert partial.complete is False
    assert partial.quarters == 2
    assert partial.revenue == 300  # YTD, not scaled up
    assert partial.revenue_yoy_pct == pytest.approx(50.0)  # vs the first two quarters of 2024
    assert partial.roe_pct is None  # a half-year ROE is not comparable
    assert "前2季" in partial.label


def test_tw_missing_inputs_give_none_not_zero() -> None:
    rows = build_tw_annual_rows(_tw_income(2024, [100] * 4), [], [], years=5)

    assert rows[0].roe_pct is None
    assert rows[0].debt_ratio_pct is None
    assert rows[0].current_ratio is None
    assert rows[0].free_cash_flow is None


def test_tw_keeps_only_the_newest_years() -> None:
    income = []
    for year in range(2019, 2026):
        income += _tw_income(year, [100] * 4)

    assert [row.year for row in build_tw_annual_rows(income, [], [], years=5)] == [
        2021,
        2022,
        2023,
        2024,
        2025,
    ]


def _us(end: date, item: str, value: float, kind: str):
    return _line(end, item, value, period_type=kind, market=Market.US)


def test_us_annual_rows_use_yahoo_item_names_and_average_equity() -> None:
    y1, y2 = date(2023, 9, 30), date(2024, 9, 30)
    income = [
        _us(y1, "Total Revenue", 1000, "annual"),
        _us(y2, "Total Revenue", 1100, "annual"),
        _us(y2, "Gross Profit", 440, "annual"),
        _us(y2, "Operating Income", 220, "annual"),
        _us(y2, "Net Income Common Stockholders", 165, "annual"),
        _us(y2, "Diluted EPS", 3.3, "annual"),
        _us(date(2025, 3, 31), "Total Revenue", 999, "quarterly"),  # quarterly frame ignored
    ]
    balance = [
        _us(y1, "Stockholders Equity", 400, "annual"),
        _us(y2, "Stockholders Equity", 500, "annual"),
        _us(y2, "Total Assets", 2000, "annual"),
        _us(y2, "Total Liabilities Net Minority Interest", 1500, "annual"),
        _us(y2, "Current Assets", 300, "annual"),
        _us(y2, "Current Liabilities", 200, "annual"),
    ]
    cash = [
        _us(y2, "Operating Cash Flow", 250, "annual"),
        _us(y2, "Capital Expenditure", -50, "annual"),
    ]

    rows = build_us_annual_rows(income, balance, cash, years=5)
    latest = rows[-1]

    assert [row.year for row in rows] == [2023, 2024]
    assert latest.revenue_yoy_pct == pytest.approx(10.0)
    assert latest.gross_margin_pct == pytest.approx(40.0)
    assert latest.roe_pct == pytest.approx(165 / 450 * 100)
    assert latest.debt_ratio_pct == pytest.approx(75.0)
    assert latest.current_ratio == pytest.approx(1.5)
    assert latest.free_cash_flow == 200  # derived: 250 + (-50)
    assert rows[0].revenue_yoy_pct is None


def test_us_reported_free_cash_flow_wins_over_derivation() -> None:
    end = date(2024, 9, 30)
    cash = [
        _us(end, "Operating Cash Flow", 250, "annual"),
        _us(end, "Capital Expenditure", -50, "annual"),
        _us(end, "Free Cash Flow", 199, "annual"),
    ]
    rows = build_us_annual_rows([_us(end, "Total Revenue", 1, "annual")], [], cash, years=5)

    assert rows[0].free_cash_flow == 199


def _bar(day: date, close: float) -> EODBar:
    return EODBar(
        symbol="X", market=Market.TW, trade_date=day, open=1, high=1, low=1, close=close, volume=1
    )


def _row_years(years: list[int]):
    income = [line for y in years for line in _tw_income(y, [100] * 4)]
    return build_tw_annual_rows(income, [], [], years=10)


def test_dividends_bucket_by_roc_year_for_tw_and_ex_date_year_for_us() -> None:
    rows = _row_years([2023, 2024])
    tw_events = [
        DividendEvent(
            symbol="X",
            market=Market.TW,
            announced_date=date(2024, 2, 1),
            fiscal_year="112年",
            cash_dividend=10.0,
            stock_dividend=0.5,
        ),
        DividendEvent(
            symbol="X",
            market=Market.TW,
            announced_date=date(2025, 2, 1),
            fiscal_year="113年",
            cash_dividend=12.0,
        ),
    ]
    bars = [
        EODBar(
            symbol="X",
            market=Market.TW,
            trade_date=date(2023, 12, 29),
            open=1,
            high=1,
            low=1,
            close=200,
            volume=1,
        ),
        EODBar(
            symbol="X",
            market=Market.TW,
            trade_date=date(2024, 12, 31),
            open=1,
            high=1,
            low=1,
            close=240,
            volume=1,
        ),
    ]

    out = {r.year: r for r in attach_dividends(rows, tw_events, bars, roc_fiscal_year=True)}

    assert out[2023].cash_dividend == 10.0  # 112年 -> 2023
    assert out[2023].stock_dividend == 0.5
    assert out[2023].dividend_yield_pct == pytest.approx(5.0)  # 10 / 200
    assert out[2024].cash_dividend == 12.0
    assert out[2024].dividend_yield_pct == pytest.approx(5.0)  # 12 / 240

    us_events = [
        DividendEvent(
            symbol="X",
            market=Market.US,
            announced_date=date(2024, 2, 9),
            fiscal_year="2024",
            cash_dividend=0.24,
            cash_ex_dividend_date=date(2024, 2, 9),
        ),
        DividendEvent(
            symbol="X",
            market=Market.US,
            announced_date=date(2024, 5, 10),
            fiscal_year="2024",
            cash_dividend=0.25,
            cash_ex_dividend_date=date(2024, 5, 10),
        ),
    ]
    us_out = {
        r.year: r
        for r in attach_dividends(_row_years([2024]), us_events, [], roc_fiscal_year=False)
    }
    assert us_out[2024].cash_dividend == pytest.approx(0.49)
    assert us_out[2024].dividend_yield_pct is None  # no close -> no yield, never a guess


def _month_rows(year: int, values: list[int]):
    return [
        MonthRevenueRow(
            symbol="X",
            market=Market.TW,
            trade_date=date(year, m, 10),
            country="Taiwan",
            revenue=v,
            revenue_month=m,
            revenue_year=year,
        )
        for m, v in enumerate(values, start=1)
    ]


def test_monthly_seasonality_removes_growth_and_finds_peak_and_trough() -> None:
    # Same shape every year (Q4 strong, Q1 weak) but revenue doubles yearly: the index must still
    # show the shape, not the growth.
    shape = [80, 80, 90, 95, 100, 100, 105, 110, 115, 120, 125, 180]
    rows = []
    for year, scale in ((2021, 1), (2022, 2), (2023, 4)):
        rows += _month_rows(year, [v * scale for v in shape])

    periods, summary, reason = monthly_seasonality(rows, years=5)

    assert reason is None and summary is not None
    assert summary.years_covered == 3
    assert summary.peak_periods[0] == "12月"
    assert set(summary.trough_periods) >= {"1月", "2月"}
    assert summary.peak_quarter == 4 and summary.trough_quarter == 1
    assert len(periods) == 12
    assert sum(p.deviation_pct for p in periods) == pytest.approx(0.0, abs=0.1)
    assert summary.spread_pct == pytest.approx((180 - 80) / 80 * 100, rel=0.02)


def test_seasonality_needs_two_complete_years_and_says_why() -> None:
    rows = _month_rows(2023, [100] * 12) + _month_rows(2024, [100] * 5)  # 2024 incomplete

    periods, summary, reason = monthly_seasonality(rows, years=5)

    assert periods == [] and summary is None
    assert reason is not None and "1 年" in reason


def test_quarterly_seasonality_reports_too_little_history_instead_of_guessing() -> None:
    income = [
        _us(date(2024, 3, 31), "Total Revenue", 90, "quarterly"),
        _us(date(2024, 6, 30), "Total Revenue", 85, "quarterly"),
        _us(date(2024, 9, 30), "Total Revenue", 95, "quarterly"),
        _us(date(2024, 12, 31), "Total Revenue", 120, "quarterly"),
        _us(date(2025, 3, 31), "Total Revenue", 95, "quarterly"),
    ]

    periods, summary, reason = quarterly_seasonality(income, years=5)

    assert summary is None and periods == []
    assert reason is not None and "完整年度" in reason


def test_quarterly_seasonality_with_two_complete_years() -> None:
    income = []
    for year in (2023, 2024):
        for quarter, value in enumerate((90, 85, 95, 130), start=1):
            income.append(_us(date(year, quarter * 3, 28), "Total Revenue", value, "quarterly"))

    periods, summary, reason = quarterly_seasonality(income, years=5)

    assert reason is None and summary is not None
    assert summary.peak_quarter == 4 and summary.trough_quarter == 2
    assert [p.label for p in periods] == ["Q1", "Q2", "Q3", "Q4"]


def test_a_year_in_progress_gets_no_dividend_yield() -> None:
    income = _tw_income(2024, [100] * 4) + _tw_income(2025, [150, 150])
    rows = build_tw_annual_rows(income, [], [], years=5)
    events = [
        DividendEvent(
            symbol="X",
            market=Market.TW,
            announced_date=date(2025, 5, 1),
            fiscal_year="114年",
            cash_dividend=3.0,
        )
    ]

    out = {
        r.year: r
        for r in attach_dividends(
            rows, events, [_bar(date(2025, 6, 30), 100)], roc_fiscal_year=True
        )
    }

    assert out[2025].cash_dividend == 3.0  # still reported
    assert out[2025].dividend_yield_pct is None  # but not divided into today's price


def _etf_events(*items: tuple[date, float]) -> list[DividendEvent]:
    return [
        DividendEvent(
            symbol="E",
            market=Market.TW,
            announced_date=ex,
            fiscal_year="x",
            cash_dividend=amount,
            cash_ex_dividend_date=ex,
        )
        for ex, amount in items
    ]


def test_etf_year_rows_use_year_end_prices_and_simple_total_return() -> None:
    closes = [
        (date(2023, 12, 29), 100.0),
        (date(2024, 6, 28), 110.0),
        (date(2024, 12, 31), 120.0),
    ]

    rows = build_etf_annual_rows(
        closes, closes, _etf_events((date(2024, 6, 20), 3.0)), years=5, today=date(2026, 1, 15)
    )
    by_year = {row.year: row for row in rows}

    assert by_year[2024].price_return_pct == pytest.approx(20.0)
    assert by_year[2024].cash_dividend_per_share == 3.0
    assert by_year[2024].dividend_yield_pct == pytest.approx(2.5)  # 3 / 120
    assert by_year[2024].total_return_simple_pct == pytest.approx(23.0)  # (120 + 3 - 100) / 100
    assert by_year[2024].complete is True


def test_etf_split_year_does_not_distort_returns_or_yield() -> None:
    """0050-style: a 4-for-1 split mid-2025. Raw prices collapse 4x, dividends before the split were
    paid on the old (4x bigger) share — both must land on the same adjusted basis."""
    raw = [
        (date(2024, 12, 30), 200.0),
        (date(2025, 1, 17), 204.0),  # pre-split ex-dividend day
        (date(2025, 6, 17), 220.0),
        (date(2025, 6, 18), 55.0),  # split
        (date(2025, 12, 30), 60.0),
    ]
    adjusted, breaks = adjust_discontinuities(raw, max_abs_move=0.105)
    assert len(breaks) == 1

    rows = build_etf_annual_rows(
        raw, adjusted, _etf_events((date(2025, 1, 17), 2.0)), years=5, today=date(2026, 3, 1)
    )
    row = next(r for r in rows if r.year == 2025)

    assert row.price_return_pct == pytest.approx(20.0)  # 50 -> 60 on the post-split basis
    assert row.dividend_yield_pct == pytest.approx(
        0.5 / 60 * 100, rel=0.02
    )  # 2.0 pre-split = 0.5 now
    assert row.total_return_simple_pct == pytest.approx((60 + 0.5 - 50) / 50 * 100, rel=0.02)


def test_etf_current_and_first_partial_years_are_flagged_and_get_no_yield() -> None:
    closes = [(date(2025, 7, 1), 50.0), (date(2025, 12, 30), 55.0), (date(2026, 2, 2), 58.0)]

    rows = build_etf_annual_rows(
        closes, closes, _etf_events((date(2026, 1, 20), 1.0)), years=5, today=date(2026, 2, 10)
    )
    by_year = {row.year: row for row in rows}

    assert by_year[2025].complete is False  # listed mid-year: no prior year-end to measure from
    assert "部分" in by_year[2025].label
    assert by_year[2026].complete is False
    assert "年初至今" in by_year[2026].label
    assert by_year[2026].dividend_yield_pct is None
    assert by_year[2026].cash_dividend_per_share == 1.0  # still reported


def test_etf_without_prices_is_empty() -> None:
    assert build_etf_annual_rows([], [], [], years=5, today=date(2026, 1, 1)) == []
