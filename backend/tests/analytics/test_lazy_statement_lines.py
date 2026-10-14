"""`LazyStatementLines` gives each symbol the lines the eager parse gave, one symbol at a time.

The Taiwan statement window is 1.48M rows. Building every `FinancialStatementLine` up front took
the scoring batch to 3.06 GB (measured on the dev data) and got it OOM-killed at a 4 GB cap; scoring
only needs one symbol's lines at a time. The same equality was checked on the real data for all
2,665 symbols; this keeps it checked without it."""

from __future__ import annotations

import pandas as pd

from analytics.fundamentals import parse_statement_lines
from analytics.scoring_batch.inputs import (
    TW_STATEMENT_COLUMNS,
    lazy_statement_lines,
    statement_lines_by_symbol,
)


def _row(symbol: str, period_end: str, item: str, value: float | None, kind: str = "income"):
    return {
        "symbol": symbol,
        "market": "TW",
        "period_end": period_end,
        "statement_type": kind,
        "item": item,
        "origin_name": f"label of {item}",
        "value": value,
    }


_INCOME = [
    _row("2330", "2024-03-31", "Revenue", 100.0),
    _row("2317", "2024-03-31", "Revenue", 50.0),
    _row("2330", "2024-03-31", "EPS", 2.5),
    _row("2330", "2023-12-31", "Revenue", 90.0),
    _row("2317", "2024-03-31", "EPS", None),  # no value: the eager parse skips it
]
_BALANCE = [
    _row("2330", "2024-03-31", "TotalAssets", 900.0, "balance_sheet"),
    _row("2317", "2024-03-31", "TotalAssets", 400.0, "balance_sheet"),
]


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(TW_STATEMENT_COLUMNS))


def test_each_symbol_gets_the_same_lines_in_the_same_order_as_the_eager_parse() -> None:
    lazy = lazy_statement_lines([_frame(list(_INCOME)), _frame(list(_BALANCE))])
    eager = statement_lines_by_symbol([*_INCOME, *_BALANCE])

    assert set(lazy) == set(eager)
    for symbol in eager:
        assert lazy[symbol] == eager[symbol]


def test_a_missing_value_is_skipped_not_turned_into_nan() -> None:
    lazy = lazy_statement_lines([_frame(list(_INCOME))])

    assert [line.item for line in lazy["2317"]] == ["Revenue"]  # the None-value EPS row is dropped


def test_it_behaves_as_a_mapping_so_scoring_can_use_get_and_keys() -> None:
    lazy = lazy_statement_lines([_frame(list(_INCOME))])

    assert len(lazy) == 2
    assert lazy.get("9999", []) == []
    assert "2330" in lazy
    assert sorted(lazy.keys()) == ["2317", "2330"]


def test_a_symbol_is_only_parsed_when_asked_for(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    parsed: list[int] = []
    real = parse_statement_lines

    def counting(rows):  # type: ignore[no-untyped-def]
        parsed.append(len(rows))
        return real(rows)

    monkeypatch.setattr("analytics.scoring_batch.inputs.parse_statement_lines", counting)
    lazy = lazy_statement_lines([_frame(list(_INCOME))])

    assert parsed == []  # building it parsed nothing
    lazy["2330"]
    assert parsed == [3]  # only that symbol's three rows
