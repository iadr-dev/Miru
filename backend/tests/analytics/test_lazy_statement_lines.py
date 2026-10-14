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


# ---- US: the same lines, a symbol list that matches, and a defined order ------------------------


def _us_row(symbol: str, period: str, period_type: str | None, item: str, value: float | None):
    return {
        "symbol": symbol,
        "market": "US",
        "period_end": period,
        "statement_type": "income_statement",
        "period_type": period_type,
        "item": item,
        "origin_name": item,
        "value": value,
    }


def test_a_symbol_whose_rows_are_all_unusable_is_not_listed() -> None:
    """The scoring universe is built from the symbols this lists. Rows the model rejects (a null
    period_type from an older snapshot, a null value) used to leave their symbol out; listing it
    anyway added symbols that had nothing to score (362 of 1,488 on the dev data)."""
    from analytics.scoring_batch.inputs import STATEMENT_COLUMNS  # noqa: PLC0415

    rows = [
        _us_row("AAPL", "2024-03-31", "quarterly", "NetIncome", 10.0),
        _us_row("OLD", "2022-10-31", None, "NetIncome", 5.0),  # an older snapshot: no period_type
        _us_row("NOVALUE", "2024-03-31", "quarterly", "NetIncome", None),
    ]
    lazy = lazy_statement_lines([pd.DataFrame(rows, columns=list(STATEMENT_COLUMNS))])

    assert sorted(lazy) == ["AAPL"]
    assert sorted(statement_lines_by_symbol(rows)) == ["AAPL"]  # as the eager parse had it


def test_quarterly_comes_before_annual_for_the_same_period_and_item(tmp_path: object) -> None:
    """At a fiscal year end a symbol has the same item for the same period as both a quarterly and
    an annual line. The fundamentals code takes the first match and the TTM sum adds quarterly
    figures, so the read must say which comes first: the query's own row order differed between
    two runs over the same files (measured)."""
    from datetime import date  # noqa: PLC0415

    from analytics.scoring_batch.inputs import (  # noqa: PLC0415
        STATEMENT_COLUMNS,
        STATEMENT_KEY_COLUMNS,
        US_STATEMENT_ORDER,
    )
    from ingest.store import ParquetStore  # noqa: PLC0415

    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    rows = [
        _us_row("AAPL", "2023-12-31", "annual", "NetIncome", 400.0),
        _us_row("AAPL", "2023-12-31", "ttm", "NetIncome", 390.0),
        _us_row("AAPL", "2023-12-31", "quarterly", "NetIncome", 100.0),
    ]
    store.write_partition("us_financials", "US", date(2024, 1, 5), rows)

    for _ in range(5):
        frame = store.read_range_latest_by_key_frame(
            "us_financials",
            "US",
            [date(2024, 1, 5)],
            columns=STATEMENT_COLUMNS,
            key_columns=STATEMENT_KEY_COLUMNS,
            order_by=US_STATEMENT_ORDER,
        )
        assert frame["period_type"].tolist() == ["quarterly", "ttm", "annual"]
        lazy = lazy_statement_lines([frame])
        assert [line.value for line in lazy["AAPL"]] == [100.0, 390.0, 400.0]


def test_the_newest_snapshot_of_a_line_wins_and_a_gap_is_empty(tmp_path: object) -> None:
    from datetime import date  # noqa: PLC0415

    from analytics.scoring_batch.inputs import (  # noqa: PLC0415
        STATEMENT_COLUMNS,
        STATEMENT_KEY_COLUMNS,
    )
    from ingest.store import ParquetStore  # noqa: PLC0415

    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "us_financials",
        "US",
        date(2024, 1, 5),
        [_us_row("AAPL", "2023-12-31", "quarterly", "NetIncome", 100.0)],
    )
    store.write_partition(
        "us_financials",
        "US",
        date(2024, 1, 12),
        [_us_row("AAPL", "2023-12-31", "quarterly", "NetIncome", 105.0)],
    )  # restated

    frame = store.read_range_latest_by_key_frame(
        "us_financials",
        "US",
        [date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 19)],  # 01-19 never written
        columns=STATEMENT_COLUMNS,
        key_columns=STATEMENT_KEY_COLUMNS,
        order_by="symbol",
    )
    nothing = store.read_range_latest_by_key_frame(
        "us_financials",
        "US",
        [date(2030, 1, 1)],
        columns=STATEMENT_COLUMNS,
        key_columns=STATEMENT_KEY_COLUMNS,
        order_by="symbol",
    )

    assert frame["value"].tolist() == [105.0]
    assert len(nothing) == 0
