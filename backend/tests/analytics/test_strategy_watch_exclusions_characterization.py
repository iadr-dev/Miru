"""Pins `excluded_symbols` precedence, boundaries and malformed-row handling."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from analytics.strategy_watch import context

RUN = date(2024, 7, 1)
AS_OF = date(2024, 6, 28)
UNTIL = date(2024, 7, 4)


def _exclude(
    *,
    attention: list[Mapping[str, object]] | None = None,
    disposition: list[Mapping[str, object]] | None = None,
    dividend: list[Mapping[str, object]] | None = None,
) -> dict[str, str]:
    return context.excluded_symbols(
        run_date=RUN,
        as_of=AS_OF,
        ex_dividend_until=UNTIL,
        attention_rows=attention or [],
        disposition_rows=disposition or [],
        dividend_rows=dividend or [],
    )


def test_no_rows_gives_no_exclusions() -> None:
    assert _exclude() == {}


def test_disposition_beats_attention_and_dividend_for_the_same_symbol() -> None:
    reasons = _exclude(
        disposition=[{"symbol": "1", "period_start": "2024-06-30", "period_end": "2024-07-02"}],
        attention=[{"symbol": "1", "flagged_date": "2024-06-28"}],
        dividend=[{"symbol": "1", "cash_ex_dividend_date": "2024-07-02"}],
    )
    assert reasons == {"1": "disposition"}


def test_attention_beats_ex_dividend() -> None:
    reasons = _exclude(
        attention=[{"symbol": "1", "flagged_date": "2024-06-28"}],
        dividend=[{"symbol": "1", "stock_ex_dividend_date": "2024-07-02"}],
    )
    assert reasons == {"1": "attention"}


def test_disposition_window_is_inclusive_and_needs_both_dates() -> None:
    reasons = _exclude(
        disposition=[
            {"symbol": "start", "period_start": "2024-07-01", "period_end": "2024-07-09"},
            {"symbol": "end", "period_start": "2024-06-20", "period_end": "2024-07-01"},
            {"symbol": "future", "period_start": "2024-07-02", "period_end": "2024-07-09"},
            {"symbol": "no_end", "period_start": "2024-06-20"},
            {"symbol": "no_start", "period_end": "2024-07-09"},
        ]
    )
    assert reasons == {"start": "disposition", "end": "disposition"}


def test_dividend_window_is_inclusive_and_either_date_column_counts() -> None:
    reasons = _exclude(
        dividend=[
            {"symbol": "today", "cash_ex_dividend_date": "2024-07-01"},
            {"symbol": "last", "stock_ex_dividend_date": "2024-07-04"},
            {"symbol": "past", "cash_ex_dividend_date": "2024-06-30"},
            {"symbol": "late", "cash_ex_dividend_date": "2024-07-05"},
            {
                "symbol": "both",
                "cash_ex_dividend_date": "2024-08-01",
                "stock_ex_dividend_date": "2024-07-02",
            },
            {"symbol": "blank"},
        ]
    )
    assert reasons == {"today": "ex_dividend", "last": "ex_dividend", "both": "ex_dividend"}


def test_attention_only_counts_the_exact_last_session() -> None:
    reasons = _exclude(
        attention=[
            {"symbol": "hit", "flagged_date": "2024-06-28"},
            {"symbol": "older", "flagged_date": "2024-06-27"},
            {"symbol": "never"},
        ]
    )
    assert reasons == {"hit": "attention"}


def test_rows_without_a_string_symbol_are_skipped_everywhere() -> None:
    reasons = _exclude(
        disposition=[{"symbol": 1, "period_start": "2024-06-30", "period_end": "2024-07-02"}],
        attention=[{"symbol": None, "flagged_date": "2024-06-28"}],
        dividend=[
            {"symbol": 3, "cash_ex_dividend_date": "2024-07-02"},
            {"cash_ex_dividend_date": "2024-07-02"},
        ],
    )
    assert reasons == {}


def test_date_objects_and_unparseable_values_are_handled_like_the_iso_strings() -> None:
    reasons = _exclude(
        attention=[
            {"symbol": "obj", "flagged_date": AS_OF},
            {"symbol": "bad", "flagged_date": "junk"},
        ],
        dividend=[{"symbol": "div", "cash_ex_dividend_date": date(2024, 7, 3)}],
    )
    assert reasons == {"obj": "attention", "div": "ex_dividend"}
