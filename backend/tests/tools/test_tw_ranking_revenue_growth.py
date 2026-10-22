"""Characterization of `tw_ranking_tools._revenue_growth_rows` — the per-symbol YoY match over
every ingested month-revenue partition, with each way a symbol can drop out of the ranking."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.jobs import MONTH_REVENUE_JOB
from ingest.store import ParquetStore
from tools.tw_ranking_tools import TwRankingKind, _revenue_growth_rows

_LATEST = date(2025, 6, 10)
_NAMES = {"1101": "A", "1102": "B", "1103": "C"}


def _row(symbol: object, revenue: object, month: object, year: object) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": "2025-06-10",
        "country": "TW",
        "revenue": revenue,
        "revenue_month": month,
        "revenue_year": year,
        "create_time": None,
    }


def _write(store: ParquetStore, day: date, rows: list[dict[str, object]]) -> None:
    store.write_partition(MONTH_REVENUE_JOB.dataset, "TW", day, rows)


def test_no_partitions_is_unavailable_without_a_trade_date(tmp_path: Path) -> None:
    out = _revenue_growth_rows(ParquetStore(root=tmp_path), _NAMES, 10)
    assert out.kind is TwRankingKind.REVENUE_GROWTH
    assert out.data_available is False
    assert out.trade_date is None
    assert out.unavailable_reason
    assert out.rows == ()


def test_latest_partition_without_symbols_is_unavailable_with_trade_date(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write(store, _LATEST, [_row(None, 1, 5, 2025), _row("", 1, 5, 2025)])
    out = _revenue_growth_rows(store, _NAMES, 10)
    assert out.data_available is False
    assert out.trade_date == _LATEST
    assert out.rows == ()


def test_ranks_descending_with_names_labels_and_top_n(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write(
        store,
        _LATEST,
        [_row("1101", 150, 5, 2025), _row("1102", 90, 5, 2025), _row("1103", 300, 5, 2025)],
    )
    _write(
        store,
        date(2024, 6, 10),
        [_row("1101", 100, 5, 2024), _row("1102", 100, 5, 2024), _row("1103", 100, 5, 2024)],
    )
    out = _revenue_growth_rows(store, {"1101": "A", "1102": "B"}, 2)
    assert out.data_available is True
    assert out.trade_date == _LATEST
    assert [r.symbol for r in out.rows] == ["1103", "1101"]
    assert [r.name for r in out.rows] == [None, "A"]
    assert [r.extra_value for r in out.rows] == [200.0, 50.0]
    assert [r.extra_label for r in out.rows] == ["+200.0%", "+50.0%"]
    full = _revenue_growth_rows(store, _NAMES, 10)
    assert [r.extra_label for r in full.rows] == ["+200.0%", "+50.0%", "-10.0%"]


def test_target_symbol_returns_only_that_symbol_ignoring_top_n(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write(store, _LATEST, [_row("1101", 150, 5, 2025), _row("1102", 90, 5, 2025)])
    _write(store, date(2024, 6, 10), [_row("1101", 100, 5, 2024), _row("1102", 100, 5, 2024)])
    out = _revenue_growth_rows(store, _NAMES, 1, "1102")
    assert [r.symbol for r in out.rows] == ["1102"]
    assert out.data_available is True
    missing = _revenue_growth_rows(store, _NAMES, 1, "9999")
    assert missing.rows == ()
    assert missing.data_available is True


def test_symbols_without_a_usable_year_ago_figure_are_dropped(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write(
        store,
        _LATEST,
        [
            _row("1101", 120, 5, 2025),  # ok
            _row("1102", 120, 5, 2025),  # year-ago revenue is zero
            _row("1103", 120, 5, 2025),  # no year-ago row at all
            _row("2001", 120, 5, 2025),  # year-ago revenue is missing
            _row("2002", None, 5, 2025),  # latest revenue is not a number
            _row("2005", 120, 5, 2025),  # year-ago row is for a different month
        ],
    )
    _write(
        store,
        date(2024, 6, 10),
        [
            _row("1101", 100, 5, 2024),
            _row("1102", 0, 5, 2024),
            _row("2001", None, 5, 2024),
            _row("2002", 100, 5, 2024),
            _row("2005", 100, 4, 2024),
            _row(None, 100, 5, 2024),
            _row("7777", 100, 5, 2024),  # not in the latest partition at all
        ],
    )
    out = _revenue_growth_rows(store, _NAMES, 10)
    assert [r.symbol for r in out.rows] == ["1101"]
    assert out.rows[0].extra_value == 20.0


def test_year_ago_filing_is_found_in_any_partition_and_the_first_match_wins(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    _write(store, _LATEST, [_row("1101", 200, 5, 2025), _row("1102", 200, 5, 2025)])
    # 1101's year-ago filing sits in two older partitions; the earlier date is scanned first.
    _write(store, date(2024, 6, 5), [_row("1101", 100, 5, 2024)])
    _write(store, date(2024, 7, 5), [_row("1101", 50, 5, 2024), _row("1102", 160, 5, 2024)])
    _write(store, date(2024, 8, 5), [_row("1102", 10, 5, 2024)])
    out = _revenue_growth_rows(store, _NAMES, 10)
    by_symbol = {r.symbol: r.extra_value for r in out.rows}
    assert by_symbol == {"1101": 100.0, "1102": 25.0}
    assert [r.symbol for r in out.rows] == ["1101", "1102"]


def test_a_null_year_in_a_partition_leaves_no_year_over_year_match(tmp_path: Path) -> None:
    # A null in the month/year columns makes the stored column a float, so no row of that
    # partition has an int month/year and none of its symbols gets a year-ago target.
    store = ParquetStore(root=tmp_path)
    _write(store, _LATEST, [_row("1101", 120, 5, 2025), _row("1102", 120, 5, None)])
    _write(store, date(2024, 6, 10), [_row("1101", 100, 5, 2024), _row("1102", 100, 5, 2024)])
    out = _revenue_growth_rows(store, _NAMES, 10)
    assert out.data_available is True
    assert out.rows == ()
