"""Characterization of `analytics.screener.engine._gated_etf_universe`: the gated, merged TW-ETF
universe the screener's ETF setup stage filters over."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from analytics.screener.engine import _gated_etf_universe
from analytics.screener.gates import GateDefaults
from ingest.store import ParquetStore

_AS_OF = date(2026, 10, 16)  # a Friday
_PREV = date(2026, 10, 9)


def _nav(symbol: object, close: object = 100.0, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {"symbol": symbol, "market": "TW", "close_price": close}
    row.update(extra)
    return row


def _price(symbol: str, close: float) -> dict[str, object]:
    return {"symbol": symbol, "market": "TW", "close": close, "volume": 1}


def _dividend(symbol: str, ex_date: date, cash: float | None) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "announced_date": ex_date.isoformat(),
        "fiscal_year": "2026",
        "cash_dividend": cash,
        "cash_ex_dividend_date": ex_date.isoformat(),
    }


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(root=tmp_path)


def _write(store: ParquetStore, dataset: str, day: date, rows: list[dict[str, object]]) -> None:
    store.write_partition(dataset, "TW", day, rows)


def test_without_any_nav_partition_there_is_no_universe(store: ParquetStore) -> None:
    assert _gated_etf_universe(store, GateDefaults()) == ({}, None)


def test_the_latest_nav_partition_defines_the_as_of_and_the_rows(store: ParquetStore) -> None:
    _write(store, "etf_nav", _PREV, [_nav("0050"), _nav("0056")])
    _write(store, "etf_nav", _AS_OF, [_nav("0050", 150.0, name="元大台灣50")])
    merged, as_of = _gated_etf_universe(store, GateDefaults())
    assert as_of == _AS_OF
    assert list(merged) == ["0050"]
    row = merged["0050"]
    assert row["close_price"] == 150.0  # the nav row passes through
    assert row["name"] == "元大台灣50"
    assert row["symbol_type"] == "etf"
    assert row["aum"] is None
    assert row["expense_ratio_pct_approx"] is None
    assert row["distribution_yield_ttm_pct"] is None
    assert row["distribution_frequency"] is None


def test_rows_without_a_usable_symbol_are_skipped(store: ParquetStore) -> None:
    rows = [_nav(None), _nav(""), {"market": "TW", "close_price": 1.0}, _nav("0050")]
    _write(store, "etf_nav", _AS_OF, rows)
    merged, _ = _gated_etf_universe(store, GateDefaults())
    assert list(merged) == ["0050"]


def test_min_price_drops_cheaper_etfs_but_keeps_an_unpriced_one(store: ParquetStore) -> None:
    _write(
        store,
        "etf_nav",
        _AS_OF,
        [_nav("0050", 9.99), _nav("0056", 10.0), _nav("00878", None), _nav("00919", None)],
    )
    merged, _ = _gated_etf_universe(store, GateDefaults(min_price=10.0))
    assert sorted(merged) == ["0056", "00878", "00919"]


def test_leveraged_and_inverse_etfs_are_excluded_only_when_asked(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050"), _nav("00631L"), _nav("00632R")])
    kept, _ = _gated_etf_universe(store, GateDefaults())
    assert sorted(kept) == ["0050", "00631L", "00632R"]
    assert kept["00631L"]["symbol_type"] == "etf_leveraged"
    assert kept["00632R"]["symbol_type"] == "etf_inverse"
    gated, _ = _gated_etf_universe(store, GateDefaults(exclude_leveraged_inverse=True))
    assert list(gated) == ["0050"]


def test_min_aum_needs_a_known_aum_at_or_above_the_floor(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050"), _nav("0056"), _nav("00878"), _nav("00919")])
    _write(store, "etf_aum", _PREV, [{"symbol": "00919", "market": "TW", "aum": 999.0}])
    _write(
        store,
        "etf_aum",
        _AS_OF,
        [
            {"symbol": "0050", "market": "TW", "aum": 5000},
            {"symbol": "0056", "market": "TW", "aum": 50.0},
            {"symbol": "00878", "market": "TW", "aum": None},
            {"symbol": None, "market": "TW", "aum": 1.0},
        ],
    )
    merged, _ = _gated_etf_universe(store, GateDefaults(min_aum=100.0))
    assert list(merged) == ["0050"]  # unknown, bad and below-floor AUM drop out
    assert merged["0050"]["aum"] == 5000.0
    assert isinstance(merged["0050"]["aum"], float)

    ungated, _ = _gated_etf_universe(store, GateDefaults())
    assert sorted(ungated) == ["0050", "0056", "00878", "00919"]
    assert ungated["0056"]["aum"] == 50.0
    assert ungated["00878"]["aum"] is None
    assert ungated["00919"]["aum"] is None  # an older AUM partition is not used


def test_the_expense_ratio_is_joined_from_the_latest_partition(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050"), _nav("0056")])
    _write(
        store,
        "etf_expense_ratio",
        _AS_OF,
        [
            {"symbol": "0050", "market": "TW", "total_expense_ratio_pct_approx": 0.46},
            {"symbol": "0056", "market": "TW", "total_expense_ratio_pct_approx": None},
        ],
    )
    merged, _ = _gated_etf_universe(store, GateDefaults())
    assert merged["0050"]["expense_ratio_pct_approx"] == 0.46
    assert merged["0056"]["expense_ratio_pct_approx"] is None


def test_distribution_yield_and_frequency_come_from_the_trailing_year(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050", 200.0), _nav("0056", None), _nav("00878", 50.0)])
    events = [
        _dividend("0050", date(2026, 1, 20), 1.0),
        _dividend("0050", date(2026, 4, 20), 2.0),
        _dividend("0050", date(2026, 7, 20), 3.0),
        _dividend("0050", date(2026, 9, 20), 4.0),
        _dividend("0050", date(2025, 9, 1), 50.0),  # outside the trailing 365 days
        _dividend("0050", date(2026, 3, 1), None),  # no cash
        _dividend("0050", date(2026, 3, 2), 0.0),  # zero cash
        _dividend("0056", date(2026, 2, 1), 2.0),
        _dividend("1101", date(2026, 2, 1), 9.0),  # a stock, not an ETF
        _dividend("00878", date(2026, 2, 1), 1.0),
        _dividend("00878", date(2026, 8, 1), 1.0),
        _dividend("00878", date(2026, 6, 1), 1.0),  # 3 events: no clean cadence
    ]
    _write(store, "TaiwanStockDividend", _AS_OF, events)
    merged, _ = _gated_etf_universe(store, GateDefaults())
    assert merged["0050"]["distribution_yield_ttm_pct"] == pytest.approx(10.0 / 200.0 * 100)
    assert merged["0050"]["distribution_frequency"] == "季配"
    # A fund with no close has distributions but no yield or cadence read.
    assert merged["0056"]["distribution_yield_ttm_pct"] is None
    assert merged["0056"]["distribution_frequency"] is None
    assert merged["00878"]["distribution_yield_ttm_pct"] == pytest.approx(3.0 / 50.0 * 100)
    assert merged["00878"]["distribution_frequency"] is None


def test_trailing_returns_are_merged_per_window(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050", 110.0), _nav("0056", 10.0)])
    _write(store, "TaiwanStockPrice", _PREV, [_price("0050", 100.0), _price("0056", 10.0)])
    _write(store, "TaiwanStockPrice", _AS_OF, [_price("0050", 110.0), _price("0056", 10.0)])
    merged, _ = _gated_etf_universe(store, GateDefaults())
    assert merged["0050"]["return_1w_pct"] == pytest.approx(10.0)
    assert merged["0050"]["return_1y_pct"] == pytest.approx(10.0)
    assert merged["0056"]["return_1w_pct"] == pytest.approx(0.0)


def test_an_etf_with_a_single_price_bar_has_no_return_fields(store: ParquetStore) -> None:
    _write(store, "etf_nav", _AS_OF, [_nav("0050")])
    _write(store, "TaiwanStockPrice", _AS_OF, [_price("0050", 110.0)])
    merged, _ = _gated_etf_universe(store, GateDefaults())
    assert "return_1w_pct" not in merged["0050"]
