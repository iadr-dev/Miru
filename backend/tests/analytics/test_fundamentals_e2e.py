"""The critical end-to-end guard for this rebuild: prove that a symbol whose financial-statement
and market-cap data is written through the REAL ingest store writer, then read back through the
REAL read-only Parquet reader, produces genuine (non-`unavailable`) quality/value `FactorScore`s
with real percentiles — not merely that the normalization helpers return correct numbers in
isolation. "Quality and value are now computable" is the whole point of the dispatch, so it is
proved against the real common-case data path (whole-market ingest store -> derived reader), the
same technique the ingest daemon's own end-to-end test uses for `price_eod`.
"""

from __future__ import annotations

from datetime import date

from analytics.fundamentals import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIALS_DATASET,
    MARKET_VALUE_DATASET,
    compute_fundamental_metrics,
    load_symbol_financials,
    load_symbol_market_value,
)
from analytics.models import ScoreProfile
from analytics.score import (
    _QUALITY_SUB_METRICS,
    _VALUE_SUB_METRICS,
    FactorInputs,
    SectorContext,
    build_score_result,
    compute_factor_breakdown,
)
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from schemas.capabilities import FinancialStatementLine, MarketValueRow
from schemas.enums import Market

_RUN_DATE = date(2024, 1, 2)
_Q = (date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30), date(2023, 3, 31))


def _income_rows(symbol: str, ni: float, oi: float, gp: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for period in _Q:
        for item, value in (
            ("IncomeAfterTaxes", ni),
            ("OperatingIncome", oi),
            ("GrossProfit", gp),
            ("PreTaxIncome", ni * 1.2),
            ("IncomeTaxExpense", ni * 0.2),
        ):
            rows.append(
                FinancialStatementLine(
                    symbol=symbol,
                    market=Market.TW,
                    period_end=period,
                    statement_type="income_statement",
                    item=item,
                    origin_name=item,
                    value=value,
                ).model_dump(mode="json")
            )
    return rows


def _balance_rows(symbol: str, equity: float, liabilities: float) -> list[dict[str, object]]:
    return [
        FinancialStatementLine(
            symbol=symbol,
            market=Market.TW,
            period_end=_Q[0],
            statement_type="balance_sheet",
            item=item,
            origin_name=item,
            value=value,
        ).model_dump(mode="json")
        for item, value in (
            ("Equity", equity),
            ("TotalLiabilities", liabilities),
            ("TotalAssets", equity + liabilities),
            ("ShortTermBorrowings", liabilities * 0.5),
            ("CashAndCashEquivalents", equity * 0.2),
        )
    ]


def _cash_flow_rows(symbol: str, ocf: float, capex: float, dep: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for period in _Q:
        for item, value in (
            ("CashFlowsFromOperatingActivities", ocf),
            ("AcquisitionOfPropertyPlantAndEquipment", capex),
            ("DepreciationAndAmortisation", dep),
        ):
            rows.append(
                FinancialStatementLine(
                    symbol=symbol,
                    market=Market.TW,
                    period_end=period,
                    statement_type="cash_flow",
                    item=item,
                    origin_name=item,
                    value=value,
                ).model_dump(mode="json")
            )
    return rows


def _market_value_row(symbol: str, mcap: int) -> dict[str, object]:
    return MarketValueRow(
        symbol=symbol, market=Market.TW, trade_date=_RUN_DATE, market_value=mcap
    ).model_dump(mode="json")


def _seed_store(store: ParquetStore) -> None:
    """Write a small three-symbol universe through the REAL writer, across all four financial
    datasets — one strong name (2330), plus two peers so the cross-section is meaningful."""
    income = (
        _income_rows("2330", ni=100.0, oi=140.0, gp=200.0)
        + _income_rows("2317", ni=40.0, oi=60.0, gp=90.0)
        + _income_rows("2454", ni=70.0, oi=100.0, gp=150.0)
    )
    balance = (
        _balance_rows("2330", equity=500.0, liabilities=150.0)
        + _balance_rows("2317", equity=400.0, liabilities=360.0)
        + _balance_rows("2454", equity=450.0, liabilities=250.0)
    )
    cash_flow = (
        _cash_flow_rows("2330", ocf=180.0, capex=40.0, dep=30.0)
        + _cash_flow_rows("2317", ocf=90.0, capex=60.0, dep=25.0)
        + _cash_flow_rows("2454", ocf=130.0, capex=50.0, dep=28.0)
    )
    market_value = [
        _market_value_row("2330", 2000),
        _market_value_row("2317", 1600),
        _market_value_row("2454", 1800),
    ]
    store.write_partition(FINANCIALS_DATASET, "TW", _RUN_DATE, income)
    store.write_partition(BALANCE_SHEET_DATASET, "TW", _RUN_DATE, balance)
    store.write_partition(CASH_FLOW_DATASET, "TW", _RUN_DATE, cash_flow)
    store.write_partition(MARKET_VALUE_DATASET, "TW", _RUN_DATE, market_value)


def _universe_for(
    reader: ParquetMarketDataReader, symbols: tuple[str, ...], sub_attrs: tuple[str, ...]
) -> dict[str, list[float]]:
    """Assemble the cross-sectional comparison universe (one value list per sub-metric attr) by
    reading each peer's real ingested financials back through the reader — exactly how a live
    orchestrator would build the universe for z-scoring."""
    universe: dict[str, list[float]] = {attr: [] for attr in sub_attrs}
    for sym in symbols:
        lines = load_symbol_financials(reader, market=Market.TW, run_dates=[_RUN_DATE], symbol=sym)
        mcap = load_symbol_market_value(reader, market=Market.TW, run_dates=[_RUN_DATE], symbol=sym)
        metrics = compute_fundamental_metrics(lines, market_value=mcap)
        for attr in sub_attrs:
            value = getattr(metrics, attr)
            if value is not None:
                universe[attr].append(value)
    return universe


def test_quality_and_value_are_genuinely_computable_end_to_end(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    _seed_store(store)

    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]
    try:
        # Read the target symbol's real ingested financials back through the real reader.
        lines = load_symbol_financials(
            reader, market=Market.TW, run_dates=[_RUN_DATE], symbol="2330"
        )
        mcap = load_symbol_market_value(
            reader, market=Market.TW, run_dates=[_RUN_DATE], symbol="2330"
        )
        assert lines  # the writer->reader round-trip actually returned rows
        assert mcap == 2000.0

        metrics = compute_fundamental_metrics(lines, market_value=mcap)
        # Real ratios assembled from real round-tripped line items — not None.
        assert metrics.roe is not None
        assert metrics.debt_to_equity is not None
        assert metrics.ev_ebitda is not None
        assert metrics.fcf_yield is not None
        assert metrics.roic is not None
        assert metrics.gross_profitability is not None

        symbols = ("2330", "2317", "2454")
        quality_universe = _universe_for(
            reader, symbols, tuple(s.attr for s in _QUALITY_SUB_METRICS)
        )
        value_universe = _universe_for(reader, symbols, tuple(s.attr for s in _VALUE_SUB_METRICS))

        breakdown = compute_factor_breakdown(
            FactorInputs(
                closes=[100.0] * 5,  # price factors intentionally short -> unavailable, isolating
                ma_series={"ma50": [None] * 5, "ma150": [None] * 5, "ma200": [None] * 5},
                momentum_universe=[],
                flow_value=None,
                flow_universe=[],
                price=None,
                ma50=None,
                fundamentals=metrics,
                quality_universe=quality_universe,
                value_universe=value_universe,
            )
        )
    finally:
        reader.close()

    # The whole point of the dispatch: these are now REAL, non-unavailable factors with real
    # percentiles, sourced end-to-end from the real ingest store.
    assert breakdown.quality.percentile is not None
    assert breakdown.quality.unavailable_reason is None
    assert breakdown.value_guard.percentile is not None
    assert breakdown.value_guard.unavailable_reason is None

    result = build_score_result(
        symbol="2330",
        market=Market.TW,
        as_of=_RUN_DATE,
        profile=ScoreProfile.POSITION,
        breakdown=breakdown,
        sector_context=SectorContext(sector_relative=False),
    )
    # Composite is computable off the two available fundamental factors; price factors dropped.
    assert result.composite is not None
    assert "quality" not in result.partial_factors
    assert "value_guard" not in result.partial_factors
    # No ingested sector classification -> whole-universe fallback, honestly disclosed.
    assert "whole" in result.normalization_caveat.lower()


def test_missing_financials_and_no_sector_degrades_cleanly(tmp_path: object) -> None:
    """The genuine edge case: a symbol with neither financials nor sector data. Quality/value
    must drop-and-renormalize (not fabricate), and the normalization caveat must say fundamentals
    were not normalized at all."""
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type] — empty store
    try:
        lines = load_symbol_financials(
            reader, market=Market.TW, run_dates=[_RUN_DATE], symbol="9999"
        )
        mcap = load_symbol_market_value(
            reader, market=Market.TW, run_dates=[_RUN_DATE], symbol="9999"
        )
    finally:
        reader.close()
    assert lines == []
    assert mcap is None

    metrics = compute_fundamental_metrics(lines, market_value=mcap)
    breakdown = compute_factor_breakdown(
        FactorInputs(
            closes=[100.0] * 5,
            ma_series={"ma50": [None] * 5, "ma150": [None] * 5, "ma200": [None] * 5},
            momentum_universe=[],
            flow_value=None,
            flow_universe=[],
            price=None,
            ma50=None,
            fundamentals=metrics,
        )
    )
    assert breakdown.quality.percentile is None
    assert breakdown.value_guard.percentile is None

    result = build_score_result(
        symbol="9999",
        market=Market.TW,
        as_of=_RUN_DATE,
        profile=ScoreProfile.POSITION,
        breakdown=breakdown,
    )
    assert result.composite is None  # nothing available at all -> honest None, not a fake neutral
    assert "quality" in result.partial_factors
    assert "value_guard" in result.partial_factors
    assert "not normalized" in result.normalization_caveat.lower()
