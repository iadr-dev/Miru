"""Fundamentals assembly tests: ratios are built correctly from raw line items across the three
statement families, flow metrics sum to TTM (or annualize a single quarter, disclosed), stock
metrics are point-in-time, and every ratio degrades independently when a line item is absent —
never a fabricated stand-in."""

from __future__ import annotations

from datetime import date

from analytics.fundamentals import (
    FundamentalMetrics,
    compute_fundamental_metrics,
    compute_revenue_yoy,
    load_symbol_financials,
    load_symbol_market_value,
    load_symbol_month_revenue,
    month_revenue_published_on,
    parse_month_revenue_rows,
)
from schemas.capabilities import FinancialStatementLine, MonthRevenueRow
from schemas.enums import Market

_Q = (date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30), date(2023, 3, 31))


def _line(item: str, value: float, period_end: date, statement_type: str) -> FinancialStatementLine:
    return FinancialStatementLine(
        symbol="2330",
        market=Market.TW,
        period_end=period_end,
        statement_type=statement_type,
        item=item,
        origin_name=item,
        value=value,
    )


def _four_quarter_flow(
    item: str, values: tuple[float, float, float, float], stmt: str
) -> list[FinancialStatementLine]:
    return [_line(item, v, period, stmt) for v, period in zip(values, _Q, strict=True)]


def test_roe_uses_ttm_net_income_over_latest_equity() -> None:
    lines = [
        *_four_quarter_flow("IncomeAfterTaxes", (25.0, 25.0, 25.0, 25.0), "income_statement"),
        _line("Equity", 500.0, _Q[0], "balance_sheet"),
        _line("Equity", 400.0, _Q[1], "balance_sheet"),  # older; must NOT be used
    ]
    metrics = compute_fundamental_metrics(lines, market_value=None)
    # TTM net income = 100; latest equity = 500 -> ROE 0.2
    assert metrics.roe == 0.2
    assert metrics.ttm_is_annualized is False


def test_single_quarter_net_income_is_annualized_and_disclosed() -> None:
    lines = [
        _line("IncomeAfterTaxes", 25.0, _Q[0], "income_statement"),
        _line("Equity", 500.0, _Q[0], "balance_sheet"),
    ]
    metrics = compute_fundamental_metrics(lines, market_value=None)
    # Only one quarter -> annualized x4 = 100 -> ROE 0.2, flagged annualized.
    assert metrics.roe == 0.2
    assert metrics.ttm_is_annualized is True


def test_debt_to_equity_is_total_liabilities_over_equity() -> None:
    lines = [
        _line("TotalLiabilities", 300.0, _Q[0], "balance_sheet"),
        _line("Equity", 600.0, _Q[0], "balance_sheet"),
    ]
    metrics = compute_fundamental_metrics(lines, market_value=None)
    assert metrics.debt_to_equity == 0.5


def test_gross_profitability_is_ttm_gross_profit_over_total_assets() -> None:
    lines = [
        *_four_quarter_flow("GrossProfit", (50.0, 50.0, 50.0, 50.0), "income_statement"),
        _line("TotalAssets", 1000.0, _Q[0], "balance_sheet"),
    ]
    metrics = compute_fundamental_metrics(lines, market_value=None)
    assert metrics.gross_profitability == 0.2  # 200 / 1000


def test_gross_margin_is_ttm_gross_profit_over_ttm_revenue() -> None:
    lines = [
        *_four_quarter_flow("GrossProfit", (50.0, 50.0, 50.0, 50.0), "income_statement"),
        *_four_quarter_flow("Revenue", (200.0, 200.0, 200.0, 200.0), "income_statement"),
    ]
    metrics = compute_fundamental_metrics(lines, market_value=None)
    assert metrics.gross_margin == 0.25  # 200 / 800


def test_earnings_variability_is_cv_of_quarterly_net_income() -> None:
    lines = _four_quarter_flow("IncomeAfterTaxes", (10.0, 20.0, 30.0, 40.0), "income_statement")
    metrics = compute_fundamental_metrics(lines, market_value=None)
    assert metrics.earnings_variability is not None
    assert metrics.earnings_variability > 0.0  # non-flat series has positive dispersion


def test_ev_ebitda_and_fcf_yield_need_market_cap_and_cash_flow() -> None:
    lines = [
        *_four_quarter_flow("OperatingIncome", (100.0, 100.0, 100.0, 100.0), "income_statement"),
        *_four_quarter_flow("DepreciationAndAmortisation", (25.0, 25.0, 25.0, 25.0), "cash_flow"),
        *_four_quarter_flow(
            "CashFlowsFromOperatingActivities", (150.0, 150.0, 150.0, 150.0), "cash_flow"
        ),
        *_four_quarter_flow(
            "AcquisitionOfPropertyPlantAndEquipment", (50.0, 50.0, 50.0, 50.0), "cash_flow"
        ),
        _line("ShortTermBorrowings", 200.0, _Q[0], "balance_sheet"),
        _line("CashAndCashEquivalents", 100.0, _Q[0], "balance_sheet"),
    ]
    metrics = compute_fundamental_metrics(lines, market_value=1000.0)
    # EBITDA = OI(400) + D&A(100) = 500; EV = mcap(1000) + debt(200) - cash(100) = 1100
    assert metrics.ev_ebitda is not None
    assert metrics.ev_ebitda == 1100.0 / 500.0
    # FCF = OCF(600) - capex(200) = 400; yield = 400/1000 = 0.4
    assert metrics.fcf_yield == 0.4


def test_metrics_degrade_independently_when_line_items_missing() -> None:
    # Only an income statement, no balance sheet / cash flow / market cap.
    lines = _four_quarter_flow("IncomeAfterTaxes", (25.0, 25.0, 25.0, 25.0), "income_statement")
    metrics = compute_fundamental_metrics(lines, market_value=None)
    assert metrics.roe is None  # no equity
    assert metrics.debt_to_equity is None
    assert metrics.ev_ebitda is None
    assert metrics.fcf_yield is None
    assert metrics.roic is None
    # earnings variability only needs the net-income series, which is present:
    assert metrics.earnings_variability is not None


def test_empty_lines_yield_all_none_metrics() -> None:
    metrics = compute_fundamental_metrics([], market_value=None)
    assert metrics == FundamentalMetrics()


class _FakeReader:
    """A `DerivedStoreReader`-shaped stand-in: returns pre-seeded rows per dataset, ignoring dates.
    Exercises `load_*` without a live Parquet store."""

    def __init__(self, by_dataset: dict[str, list[dict[str, object]]]) -> None:
        self._by_dataset = by_dataset

    def read_range(
        self, dataset: str, market: str, run_dates: object, *, symbols: object = None
    ) -> list[dict[str, object]]:
        del market, run_dates, symbols
        return self._by_dataset.get(dataset, [])


def test_load_symbol_financials_merges_three_statements_and_filters_symbol() -> None:
    reader = _FakeReader(
        {
            "TaiwanStockFinancialStatements": [
                _line("IncomeAfterTaxes", 25.0, _Q[0], "income_statement").model_dump(mode="json"),
                {
                    **_line("IncomeAfterTaxes", 1.0, _Q[0], "income_statement").model_dump(
                        mode="json"
                    ),
                    "symbol": "2317",
                },  # a different symbol — must be filtered out
            ],
            "TaiwanStockBalanceSheet": [
                _line("Equity", 500.0, _Q[0], "balance_sheet").model_dump(mode="json"),
            ],
        }
    )
    lines = load_symbol_financials(
        reader, market=Market.TW, run_dates=[date(2024, 1, 2)], symbol="2330"
    )
    assert {line.symbol for line in lines} == {"2330"}
    assert {line.item for line in lines} == {"IncomeAfterTaxes", "Equity"}


def test_load_symbol_market_value_picks_latest_and_none_when_absent() -> None:
    reader = _FakeReader(
        {
            "TaiwanStockMarketValue": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2024-01-02",
                    "market_value": 1000,
                },
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2024-01-03",
                    "market_value": 2000,
                },
            ]
        }
    )
    assert (
        load_symbol_market_value(
            reader, market=Market.TW, run_dates=[date(2024, 1, 2)], symbol="2330"
        )
        == 2000.0
    )
    assert (
        load_symbol_market_value(
            reader, market=Market.TW, run_dates=[date(2024, 1, 2)], symbol="9999"
        )
        is None
    )


def _revenue_row(year: int, month: int, revenue: int) -> dict[str, object]:
    return {
        "symbol": "2330",
        "market": "TW",
        "trade_date": date(year, month, 1).isoformat(),
        "country": "Taiwan",
        "revenue": revenue,
        "revenue_month": month,
        "revenue_year": year,
    }


def test_compute_revenue_yoy_compares_same_calendar_month_a_year_prior() -> None:
    rows = parse_month_revenue_rows(
        [_revenue_row(2023, 6, 100), _revenue_row(2024, 6, 120), _revenue_row(2024, 5, 999)]
    )
    assert compute_revenue_yoy(rows) == 0.2  # (120 - 100) / 100


def test_compute_revenue_yoy_is_none_when_no_prior_year_month() -> None:
    rows = parse_month_revenue_rows([_revenue_row(2024, 6, 120)])
    assert compute_revenue_yoy(rows) is None


def test_compute_revenue_yoy_is_none_when_no_rows() -> None:
    assert compute_revenue_yoy([]) is None


def test_load_symbol_month_revenue_filters_symbol() -> None:
    reader = _FakeReader(
        {
            "TaiwanStockMonthRevenue": [
                _revenue_row(2024, 6, 120),
                {**_revenue_row(2024, 6, 1), "symbol": "2317"},
            ]
        }
    )
    rows = load_symbol_month_revenue(
        reader, market=Market.TW, run_dates=[date(2024, 7, 1)], symbol="2330"
    )
    assert {row.symbol for row in rows} == {"2330"}
    assert rows[0].revenue == 120


def _gate_rows() -> list[MonthRevenueRow]:
    return parse_month_revenue_rows(
        [
            _revenue_row(2023, 12, 100),
            _revenue_row(2024, 12, 150),
            _revenue_row(2024, 1, 200),
            _revenue_row(2025, 1, 400),
        ]
    )


def test_month_is_usable_from_the_tenth_of_the_following_month() -> None:
    jan = parse_month_revenue_rows([_revenue_row(2025, 1, 300)])[0]
    assert month_revenue_published_on(jan) == date(2025, 2, 10)


def test_december_revenue_rolls_to_january_tenth_of_next_year() -> None:
    dec = parse_month_revenue_rows([_revenue_row(2024, 12, 150)])[0]
    assert month_revenue_published_on(dec) == date(2025, 1, 10)


def test_yoy_uses_the_latest_published_month_not_the_latest_stored_one() -> None:
    rows = _gate_rows()
    assert compute_revenue_yoy(rows, as_of=date(2025, 2, 9)) == 0.5  # Dec 2024 vs Dec 2023
    assert compute_revenue_yoy(rows, as_of=date(2025, 2, 10)) == 1.0  # Jan 2025 vs Jan 2024


def test_yoy_gate_is_inclusive_on_the_publication_day() -> None:
    rows = _gate_rows()
    assert compute_revenue_yoy(rows, as_of=date(2025, 1, 9)) is None  # Dec not yet published
    assert compute_revenue_yoy(rows, as_of=date(2025, 1, 10)) == 0.5


def test_yoy_without_as_of_is_ungated() -> None:
    assert compute_revenue_yoy(_gate_rows()) == 1.0
