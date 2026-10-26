"""Characterization of `_dividend_fill_results`: 填息/填權 resolution per ex-dividend event over
the window to the next event, and the filled / not-filled / unresolved (`None`) distinction."""

from __future__ import annotations

from datetime import date

from schemas.capabilities import DividendResultRow, EODBar
from schemas.enums import Market
from tools.fundamental_tools import DividendFillResult, _dividend_fill_results


def _event(day: date, before: float) -> DividendResultRow:
    return DividendResultRow(
        symbol="2330",
        market=Market.TW,
        trade_date=day,
        before_price=before,
        after_price=before - 5,
        stock_and_cache_dividend=5.0,
        stock_or_cache_dividend="息",
        max_price=before,
        min_price=before - 6,
        open_price=before - 5,
        reference_price=before - 5,
    )


def _bar(day: date, close: float) -> EODBar:
    return EODBar(
        symbol="2330",
        market=Market.TW,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1,
    )


def test_no_events_means_no_results() -> None:
    assert _dividend_fill_results([], [_bar(date(2024, 1, 2), 50.0)]) == ()


def test_each_event_resolves_inside_its_own_window_to_the_next_event() -> None:
    e1, e2 = date(2024, 1, 10), date(2024, 6, 10)
    events = [_event(e2, 120.0), _event(e1, 100.0)]  # deliberately unsorted
    bars = [
        _bar(date(2024, 6, 20), 110.0),  # after e2, below 120: e2 stays unfilled
        _bar(date(2024, 2, 1), 101.0),  # fills e1
        _bar(date(2024, 1, 9), 900.0),  # before e1: ignored
        _bar(e1, 900.0),  # on the ex date itself: ignored
        _bar(date(2024, 1, 15), 99.0),
        _bar(e2, 900.0),  # on e2's date: outside e1's window and not after e2
    ]

    results = _dividend_fill_results(events, bars)

    assert results == (
        DividendFillResult(
            trade_date=e1,
            before_price=100.0,
            filled=True,
            filled_date=date(2024, 2, 1),
            days_to_fill=22,
        ),
        DividendFillResult(
            trade_date=e2, before_price=120.0, filled=False, filled_date=None, days_to_fill=None
        ),
    )


def test_a_close_equal_to_the_before_price_counts_as_filled() -> None:
    results = _dividend_fill_results(
        [_event(date(2024, 3, 1), 80.0)], [_bar(date(2024, 3, 4), 80.0)]
    )

    assert results[0].filled is True
    assert results[0].filled_date == date(2024, 3, 4)
    assert results[0].days_to_fill == 3


def test_the_first_qualifying_bar_wins_not_the_highest() -> None:
    results = _dividend_fill_results(
        [_event(date(2024, 3, 1), 80.0)],
        [_bar(date(2024, 3, 5), 95.0), _bar(date(2024, 3, 4), 81.0)],
    )

    assert results[0].filled_date == date(2024, 3, 4)


def test_no_later_bar_at_all_is_unresolved_not_failed() -> None:
    results = _dividend_fill_results(
        [_event(date(2024, 3, 1), 80.0)], [_bar(date(2024, 2, 20), 500.0)]
    )

    assert results[0].filled is None
    assert results[0].filled_date is None
    assert results[0].days_to_fill is None


def test_bars_only_past_the_next_event_leave_the_earlier_event_unresolved() -> None:
    e1, e2 = date(2024, 1, 10), date(2024, 2, 10)
    results = _dividend_fill_results(
        [_event(e1, 100.0), _event(e2, 100.0)], [_bar(date(2024, 2, 20), 150.0)]
    )

    assert results[0].filled is None  # nothing between e1 and e2
    assert results[1].filled is True
    assert results[1].days_to_fill == 10


def test_no_bars_at_all_leaves_every_event_unresolved() -> None:
    results = _dividend_fill_results(
        [_event(date(2024, 1, 10), 100.0), _event(date(2024, 2, 10), 90.0)], []
    )

    assert [r.filled for r in results] == [None, None]
