"""Characterization of `_parse_self_assessed_earnings_html` — synthetic HTML, no network."""

from __future__ import annotations

from sources.mops import (
    _parse_self_assessed_earnings_html,
    _self_assessed_period_from_title,
)

_HEADER = (
    "<tr><th>公司代號</th><th>公司簡稱</th><th>a</th><th>b</th><th>c</th><th>d</th>"
    "<th>差異原因說明</th></tr>"
)


def _title(text: str) -> str:
    return f"<table><tr><td>{text}</td><td>單位：仟元</td></tr></table>"


def _row(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _table(*rows: str) -> str:
    return "<table>" + _HEADER + "".join(rows) + "</table>"


def test_title_then_data_table_individual() -> None:
    html = _title("2025年7月（個別）") + _table(
        _row("2330", " 台積電 ", "1,000", "2,000", "300", "400", " note ")
    )
    assert _parse_self_assessed_earnings_html(html) == [
        {
            "symbol": "2330",
            "market": "TW",
            "company_name": "台積電",
            "revenue_period": "2025-07",
            "consolidated": False,
            "monthly_operating_profit": 1000,
            "ytd_operating_profit": 2000,
            "monthly_pretax_profit": 300,
            "ytd_pretax_profit": 400,
            "variance_note": "note",
        }
    ]


def test_consolidated_title_flag_and_period_padding() -> None:
    html = _title("2024年1月\n（合併）") + _table(_row("1101", "x", "1", "2", "3", "4"))
    (row,) = _parse_self_assessed_earnings_html(html)
    assert row["revenue_period"] == "2024-01"
    assert row["consolidated"] is True
    assert row["variance_note"] is None  # exactly six columns -> no note column


def test_data_table_before_any_title_is_skipped() -> None:
    html = _table(_row("2330", "x", "1", "2", "3", "4"))
    assert _parse_self_assessed_earnings_html(html) == []


def test_untitled_table_without_header_does_not_set_period() -> None:
    html = _title("no period here") + _table(_row("2330", "x", "1", "2", "3", "4"))
    assert _parse_self_assessed_earnings_html(html) == []


def test_title_applies_to_following_tables_until_replaced() -> None:
    html = (
        _title("2025年7月（個別）")
        + _table(_row("1111", "a", "1", "1", "1", "1"))
        + _table(_row("2222", "b", "2", "2", "2", "2"))
        + _title("2025年8月（合併）")
        + _table(_row("3333", "c", "3", "3", "3", "3"))
    )
    rows = _parse_self_assessed_earnings_html(html)
    assert [(r["symbol"], r["revenue_period"], r["consolidated"]) for r in rows] == [
        ("1111", "2025-07", False),
        ("2222", "2025-07", False),
        ("3333", "2025-08", True),
    ]


def test_table_with_own_title_and_header_does_not_update_period() -> None:
    inline = (
        "<table><tr><td>2030年1月（合併）</td></tr>"
        + _HEADER
        + _row("9999", "z", "1", "1", "1", "1")
        + "</table>"
    )
    # no earlier title -> current period stays unset, so the inline table is skipped
    assert _parse_self_assessed_earnings_html(inline) == []
    # earlier title wins over the inline one
    html = _title("2025年7月（個別）") + inline
    (row,) = _parse_self_assessed_earnings_html(html)
    assert (row["symbol"], row["revenue_period"], row["consolidated"]) == ("9999", "2025-07", False)


def test_row_filters_short_repeated_header_blank_and_non_digit_symbol() -> None:
    html = _title("2025年7月（個別）") + _table(
        _row("1", "short", "1", "2", "3"),  # fewer than six columns
        _row("公司代號", "公司簡稱", "a", "b", "c", "d"),  # repeated header row
        _row("", "blank", "1", "2", "3", "4"),
        _row("  ", "blank2", "1", "2", "3", "4"),
        _row("A123", "alpha", "1", "2", "3", "4"),
        _row(" 2454 ", "ok", "1", "2", "3", "4"),
    )
    rows = _parse_self_assessed_earnings_html(html)
    assert [r["symbol"] for r in rows] == ["2454"]


def test_unavailable_marker_and_non_numeric_cells_become_none() -> None:
    html = _title("2025年7月（個別）") + _table(
        _row("2836", "bank", "金融業者不適用", "不適用", "150,247", "x"),
    )
    (row,) = _parse_self_assessed_earnings_html(html)
    assert row["monthly_operating_profit"] is None
    assert row["ytd_operating_profit"] is None
    assert row["monthly_pretax_profit"] == 150247
    assert row["ytd_pretax_profit"] is None  # unparseable -> None via _to_int
    assert row["variance_note"] is None


def test_variance_note_blank_is_none_and_present_is_stripped() -> None:
    html = _title("2025年7月（個別）") + _table(
        _row("1000", "a", "1", "2", "3", "4", "   "),
        _row("2000", "b", "1", "2", "3", "4", " reason "),
    )
    rows = _parse_self_assessed_earnings_html(html)
    assert [r["variance_note"] for r in rows] == [None, "reason"]


def test_negative_values_and_empty_input() -> None:
    html = _title("2025年12月（個別）") + _table(_row("3000", "n", "-5", "-6", "-7", "-8"))
    (row,) = _parse_self_assessed_earnings_html(html)
    assert (row["monthly_operating_profit"], row["ytd_pretax_profit"]) == (-5, -8)
    assert _parse_self_assessed_earnings_html("") == []


def test_period_from_title_helper() -> None:
    assert _self_assessed_period_from_title([["x"], ["2025年7月\n（合併）"]]) == ("2025-07", True)
    assert _self_assessed_period_from_title([["2025年12月（個別）"]]) == ("2025-12", False)
    assert _self_assessed_period_from_title([["nothing"]]) is None
    assert _self_assessed_period_from_title([]) is None
