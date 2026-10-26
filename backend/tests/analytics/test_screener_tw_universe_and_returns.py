"""Characterization of `analytics.screener.engine._gated_tw_universe` (stage ⓐ gates for TW) and
`_etf_trailing_returns_by_symbol` (the shared-read multi-window ETF return), synthetic partitions
only."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest

from analytics.screener.engine import _etf_trailing_returns_by_symbol, _gated_tw_universe
from analytics.screener.gates import GateDefaults
from ingest.store import ParquetStore

_PRICE = "TaiwanStockPrice"
_ATTENTION = "attention_stocks"
_DISPOSITION = "disposition_events"
_AS_OF = date(2026, 10, 16)  # a Friday


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(root=tmp_path)


def _write(store: ParquetStore, dataset: str, day: date, rows: list[dict[str, object]]) -> None:
    store.write_partition(dataset, "TW", day, rows)


def _px(symbol: object, close: object = 50.0, volume: object = 1000.0) -> dict[str, object]:
    return {"symbol": symbol, "market": "TW", "close": close, "volume": volume}


# ---- _gated_tw_universe -----------------------------------------------------------------------


def test_no_price_partition_means_no_universe(store: ParquetStore) -> None:
    assert _gated_tw_universe(store, GateDefaults()) == (set(), None)


def test_latest_price_partition_defines_as_of_and_only_plain_stocks_survive(
    store: ParquetStore,
) -> None:
    _write(store, _PRICE, date(2026, 10, 9), [_px("1101")])
    _write(
        store,
        _PRICE,
        _AS_OF,
        [
            _px("2330"),
            _px(None),
            _px(""),
            _px("0050"),  # an ETF has its own tab
            _px("00631L"),  # leveraged ETF
            _px("030001"),  # warrant: not scoreable at all
            _px("2454"),
        ],
    )
    survivors, as_of = _gated_tw_universe(store, GateDefaults())
    assert as_of == _AS_OF
    assert survivors == {"2330", "2454"}


def test_price_and_volume_floors_are_inclusive_and_skip_missing_values(
    store: ParquetStore,
) -> None:
    rows = [
        _px("1101", close=9.99, volume=500.0),  # too cheap
        _px("1102", close=10.0, volume=500.0),  # exactly at both floors
        _px("1103", close=10.0, volume=499.0),  # too thin
        _px("1104", close=None, volume=None),  # nothing to gate on: kept
        _px("1105", close=None, volume=499.0),  # unpriced but too thin
        _px("1106", close=9.0, volume=None),  # cheap, volume unknown
    ]
    _write(store, _PRICE, _AS_OF, rows)
    gates = GateDefaults(min_price=10.0, min_avg_volume=500.0)
    survivors, _ = _gated_tw_universe(store, gates)
    assert survivors == {"1102", "1104"}


def test_attention_and_disposition_exclusions_use_the_latest_partition_only(
    store: ParquetStore,
) -> None:
    _write(store, _PRICE, _AS_OF, [_px("1101"), _px("1102"), _px("1103"), _px("1104")])
    _write(store, _ATTENTION, date(2026, 10, 15), [{"symbol": "1101"}])
    _write(store, _ATTENTION, _AS_OF, [{"symbol": "1102"}, {"symbol": None}])
    _write(store, _DISPOSITION, date(2026, 10, 14), [{"symbol": "1103"}])
    _write(store, _DISPOSITION, _AS_OF, [{"symbol": "1104"}])

    survivors, _ = _gated_tw_universe(store, GateDefaults())
    assert survivors == {"1101", "1103"}  # only the latest dated flag partitions count

    no_attention, _ = _gated_tw_universe(store, GateDefaults(exclude_attention=False))
    assert no_attention == {"1101", "1102", "1103"}
    no_disposition, _ = _gated_tw_universe(store, GateDefaults(exclude_disposition=False))
    assert no_disposition == {"1101", "1103", "1104"}
    neither, _ = _gated_tw_universe(
        store, GateDefaults(exclude_attention=False, exclude_disposition=False)
    )
    assert neither == {"1101", "1102", "1103", "1104"}


def test_missing_flag_feeds_exclude_nothing(store: ParquetStore) -> None:
    _write(store, _PRICE, _AS_OF, [_px("1101")])
    survivors, as_of = _gated_tw_universe(store, GateDefaults())
    assert (survivors, as_of) == ({"1101"}, _AS_OF)


# ---- _etf_trailing_returns_by_symbol ----------------------------------------------------------


def _write_series(store: ParquetStore, closes_by_symbol: dict[str, list[float]]) -> None:
    """One price partition per business day, ending on `_AS_OF`; each symbol's series is
    right-aligned so a shorter series simply starts later."""
    calendar = TradingDayCalendar()
    longest = max(len(closes) for closes in closes_by_symbol.values())
    days = calendar.business_days_between(
        calendar.shift_business_days(_AS_OF, -(longest - 1)), _AS_OF
    )
    assert len(days) == longest
    for index, day in enumerate(days):
        rows = [
            _px(symbol, close=closes[index - (longest - len(closes))])
            for symbol, closes in closes_by_symbol.items()
            if index >= longest - len(closes)
        ]
        _write(store, _PRICE, day, rows)


def test_each_window_uses_its_own_slice_of_one_series(store: ParquetStore) -> None:
    closes = [100.0 + i for i in range(30)]  # 100 .. 129
    _write_series(store, {"0050": closes})
    out = _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"0050"})
    assert set(out) == {"0050"}
    assert out["0050"] == {
        "return_1w_pct": pytest.approx((129 / 124 - 1) * 100),
        "return_1m_pct": pytest.approx((129 / 108 - 1) * 100),
        "return_1q_pct": pytest.approx(29.0),
        "return_6m_pct": pytest.approx(29.0),
        "return_1y_pct": pytest.approx(29.0),
    }


def test_symbols_outside_the_etf_set_and_single_point_series_are_dropped(
    store: ParquetStore,
) -> None:
    _write_series(store, {"0050": [100.0, 110.0, 121.0], "2330": [1.0, 2.0, 3.0]})
    _write(store, _PRICE, date(2026, 10, 19), [_px("00878")])  # after as_of: not in the window
    out = _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"0050", "00878", "0056"})
    assert set(out) == {"0050"}
    assert out["0050"]["return_1w_pct"] == pytest.approx(21.0)

    single = _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"2330"})
    assert set(single) == {"2330"}  # the set is the caller's, not an ETF-shape check


def test_a_single_close_is_not_enough_for_a_return(store: ParquetStore) -> None:
    _write(store, _PRICE, _AS_OF, [_px("0050", close=100.0)])
    assert _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"0050"}) == {}


def test_a_zero_first_close_omits_only_the_windows_that_start_on_it(
    store: ParquetStore,
) -> None:
    _write_series(store, {"0050": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "0056": [0.0, 5.0, 6.0]})
    out = _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"0050", "0056"})
    # 0050: only the 1-week window (last 6 points) skips the zero; every longer one starts on it.
    assert out == {"0050": {"return_1w_pct": pytest.approx((6 / 1 - 1) * 100)}}


def test_no_price_history_gives_no_returns(store: ParquetStore) -> None:
    assert _etf_trailing_returns_by_symbol(store, "TW", _AS_OF, {"0050"}) == {}
