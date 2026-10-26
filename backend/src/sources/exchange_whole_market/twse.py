"""TWSE (listed market) endpoints and parsers - the `www.twse.com.tw/rwd` daily reports.

Each parser maps one report onto EXACTLY the row shape the FinMind row mappers produce
(`sources/finmind/technical.py`, `chip_institutional.py`: same keys, units, signs and ISO
`trade_date`), so everything downstream of the store is source-agnostic.

- PRICE_EOD: afterTrading/MI_INDEX (ALLBUT0999), the `每日收盤行情` table, found by its title.
- INSTITUTIONAL_FLOW: fund/T86 (ALLBUT0999); the wide per-stock row becomes five long rows.
- MARGIN_SHORT: marginTrading/MI_MARGN (ALL: stocks and ETFs), the table with a `資券互抵` column.
- VALUATION_RATIOS: afterTrading/BWIBBU_d (ALL); a `-` PER (loss-making) is 0.0, as in FinMind.

`ALLBUT0999` excludes warrants and CBBC (about 21 000 rows a day in FinMind's dump): every reader
of these datasets already drops them (`analytics/symbol_universe.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_
from typing import Final

from schemas.enums import Capability

from ._common import (
    BarColumns,
    Endpoint,
    ExchangeFetchError,
    JsonObject,
    NoDataPublished,
    Row,
    ValuationColumns,
    as_list,
    as_object,
    bars_from_rows,
    cell,
    check_response_date,
    clean_cell,
    column_index,
    flows_from_rows,
    is_symbol,
    margin_row,
    table_rows,
    to_count,
    valuations_from_rows,
)

_BASE: Final = "https://www.twse.com.tw/rwd/zh"
# What TWSE answers (HTTP 200) on a day it published nothing: no trading, or not out yet.
_NO_DATA_MARKERS: Final = ("沒有符合條件", "查詢無資料", "很抱歉")
_MARGIN_COLUMNS: Final = 16
_MARGIN_FIRST: Final = 2  # first of the two blocks of six: margin purchase, then short sale
_SHORT_FIRST: Final = 8

# FinMind's investor names -> the T86 headers of that category's (buy, sell) share counts.
_INVESTORS: Final = {
    "Foreign_Investor": ("外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)"),
    "Foreign_Dealer_Self": ("外資自營商買進股數", "外資自營商賣出股數"),
    "Investment_Trust": ("投信買進股數", "投信賣出股數"),
    "Dealer_self": ("自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)"),
    "Dealer_Hedging": ("自營商買進股數(避險)", "自營商賣出股數(避險)"),
}


def _has_data(body: JsonObject, label: str) -> bool:
    """`True` for an answer with a report, `False` for TWSE's "nothing published" answer; any
    other `stat` is an error (a block page, a changed API) and must not be read as an empty day."""
    stat = clean_cell(body.get("stat", ""))
    if stat.upper() == "OK":
        return True
    if any(marker in stat for marker in _NO_DATA_MARKERS):
        return False
    raise ExchangeFetchError(f"{label}: unexpected stat {stat[:40]!r}")


def _flat_report(body: JsonObject, label: str) -> tuple[list[object], list[list[object]]] | None:
    """`(fields, rows)` of a report that carries them at the root (T86, BWIBBU_d)."""
    if not _has_data(body, label):
        return None
    fields = as_list(body.get("fields"))
    if fields is None:
        raise ExchangeFetchError(f"{label}: response has no 'fields'")
    return fields, table_rows(body)


def _table_report(
    body: JsonObject, label: str, *, wanted: str, by_title: bool
) -> tuple[list[object], list[list[object]]] | None:
    """`(fields, rows)` of the one table in `tables` that matches: its title contains `wanted`
    (`by_title`) or one of its headers equals `wanted`. Found by content, never by index - the
    index of the table moves between days."""
    if not _has_data(body, label):
        return None
    tables = as_list(body.get("tables")) or []
    for raw in tables:
        table = as_object(raw)
        fields = as_list(table.get("fields")) if table else None
        if table is None or not fields:
            continue
        found = (
            wanted in clean_cell(table.get("title", ""))
            if by_title
            else wanted in [clean_cell(f) for f in fields]
        )
        if found:
            return fields, table_rows(table)
    raise ExchangeFetchError(f"{label}: no table matching {wanted!r} in the response")


def parse_price(body: JsonObject, run_date: date_) -> list[Row]:
    report = _table_report(body, "MI_INDEX", wanted="每日收盤行情", by_title=True)
    if report is None:
        raise NoDataPublished
    check_response_date(body, run_date, "MI_INDEX")
    fields, rows = report
    columns = BarColumns(
        code=column_index(fields, "證券代號"),
        open=column_index(fields, "開盤價"),
        high=column_index(fields, "最高價"),
        low=column_index(fields, "最低價"),
        close=column_index(fields, "收盤價"),
        volume=column_index(fields, "成交股數"),
        turnover=column_index(fields, "成交金額"),
    )
    return bars_from_rows(rows, run_date, columns)


def parse_institutional(body: JsonObject, run_date: date_) -> list[Row]:
    report = _flat_report(body, "T86")
    if report is None:
        raise NoDataPublished
    check_response_date(body, run_date, "T86")
    fields, rows = report
    columns = {
        investor: (column_index(fields, buy), column_index(fields, sell))
        for investor, (buy, sell) in _INVESTORS.items()
    }
    code = column_index(fields, "證券代號")
    return flows_from_rows(rows, run_date, code=code, columns=columns, report="T86")


def parse_margin(body: JsonObject, run_date: date_) -> list[Row]:
    report = _table_report(body, "MI_MARGN", wanted="資券互抵", by_title=False)
    if report is None:
        raise NoDataPublished
    check_response_date(body, run_date, "MI_MARGN")
    fields, rows = report
    # Two blocks of six headers repeat the same names (買進/賣出/...): read by position, after
    # checking the layout is the known one.
    if len(fields) != _MARGIN_COLUMNS or clean_cell(fields[0]) != "代號":
        raise ExchangeFetchError("MI_MARGN: unexpected column layout")
    out: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, 0))
        if not is_symbol(symbol):
            continue  # the 合計 (total) row
        out.append(
            margin_row(
                symbol,
                run_date,
                (
                    [to_count(cell(row, _MARGIN_FIRST + i)) or 0 for i in range(6)],
                    [to_count(cell(row, _SHORT_FIRST + i)) or 0 for i in range(6)],
                ),
                offset=to_count(cell(row, 14)) or 0,
                note=clean_cell(cell(row, 15)) or None,
            )
        )
    return out


def parse_valuation(body: JsonObject, run_date: date_) -> list[Row]:
    report = _flat_report(body, "BWIBBU_d")
    if report is None:
        raise NoDataPublished
    check_response_date(body, run_date, "BWIBBU_d")
    fields, rows = report
    columns = ValuationColumns(
        code=column_index(fields, "證券代號"),
        dividend_yield=column_index(fields, "殖利率(%)"),
        per=column_index(fields, "本益比"),
        pbr=column_index(fields, "股價淨值比"),
    )
    return valuations_from_rows(rows, run_date, columns)


def _query(**fixed: str) -> Callable[[date_], dict[str, str]]:
    def build(run_date: date_) -> dict[str, str]:
        return {"date": run_date.strftime("%Y%m%d"), **fixed, "response": "json"}

    return build


ENDPOINTS: Final[dict[Capability, Endpoint]] = {
    Capability.PRICE_EOD: Endpoint(
        "MI_INDEX",
        f"{_BASE}/afterTrading/MI_INDEX",
        _query(type="ALLBUT0999"),
        parse_price,
    ),
    Capability.INSTITUTIONAL_FLOW: Endpoint(
        "T86", f"{_BASE}/fund/T86", _query(selectType="ALLBUT0999"), parse_institutional
    ),
    Capability.MARGIN_SHORT: Endpoint(
        "MI_MARGN",
        f"{_BASE}/marginTrading/MI_MARGN",
        _query(selectType="ALL"),
        parse_margin,
    ),
    Capability.VALUATION_RATIOS: Endpoint(
        "BWIBBU_d",
        f"{_BASE}/afterTrading/BWIBBU_d",
        _query(selectType="ALL"),
        parse_valuation,
    ),
}
