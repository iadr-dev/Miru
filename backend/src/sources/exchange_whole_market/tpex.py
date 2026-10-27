"""TPEx (OTC market) endpoints and parsers - the `www.tpex.org.tw/www/zh-tw` daily reports.

Same contract as `twse.py`: each parser produces the FinMind row shape. Requests carry a Gregorian
`YYYY/MM/DD` date; the answer is `{"stat": "ok", "date": "YYYYMMDD", "tables": [{...}]}` with the
report in `tables[0]` (its own `date` is ROC, which is never used: the root date is checked).

- PRICE_EOD: afterTrading/otc (EW) is the universe and the OHLC (every security except warrants),
  but its volume/turnover are regular-session ROUND LOTS only. FinMind's are the all-session
  totals, which `afterTrading/dailyQuotes` (上櫃股票行情, the same day incl. warrants) reports:
  round lots + 盤後定價 (fixed price) + 盤中零股 + 盤後零股 (odd lots), block trades excluded;
  it equals FinMind on 100% of the symbols checked. So PRICE_EOD is a `Supplement`: the
  dailyQuotes volume/turnover replace the otc ones, and if they cannot be had for EVERY bar the
  adapter reports the round-lot rows as incomplete (never as if they were whole).
- INSTITUTIONAL_FLOW: insti/dailyTrade (EW); 24 unnamed columns, read by position.
- MARGIN_SHORT: margin/balance; note the order, 券賣 comes before 券買.
- VALUATION_RATIOS: afterTrading/peQryDate; a `-` PER is 0.0, as in FinMind.
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
    NoDataPublishedError,
    Row,
    Supplement,
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

_BASE: Final = "https://www.tpex.org.tw/www/zh-tw"
_INSTITUTIONAL_COLUMNS: Final = 24
# Blocks of (buy, sell, net): 外資(不含自營) | 外資自營 | 外資合計 | 投信 | 自營(自行買賣) |
# 自營(避險) | 自營合計 | then 三大法人合計. Offsets of the BUY cell of the blocks FinMind keeps as
# its own investor categories (the sell cell follows it).
_INVESTOR_BUY_COLUMN: Final = {
    "Foreign_Investor": 2,
    "Foreign_Dealer_Self": 5,
    "Investment_Trust": 11,
    "Dealer_self": 14,
    "Dealer_Hedging": 17,
}


_MARGIN_BLOCK: Final = ("資買", "資賣", "現償", "前資餘額(張)", "資餘額", "資限額")
# TPEx lists 券賣 before 券買: buy (cover) first, the order the margin block and FinMind use.
_SHORT_BLOCK: Final = ("券買", "券賣", "券償", "前券餘額(張)", "券餘額", "券限額")
_MARGIN_HEADERS: Final = ("代號", *_MARGIN_BLOCK, *_SHORT_BLOCK, "資券相抵(張)", "備註")


def _first_table(body: JsonObject, label: str) -> tuple[list[object], list[list[object]]] | None:
    """`(fields, rows)` of the report, or `None` on a day TPEx published nothing."""
    stat = clean_cell(body.get("stat", ""))
    if stat.lower() != "ok":
        raise ExchangeFetchError(f"{label}: unexpected stat {stat[:40]!r}")
    tables = as_list(body.get("tables")) or []
    table = as_object(tables[0]) if tables else None
    fields = as_list(table.get("fields")) if table else None
    if table is None or not fields:
        raise ExchangeFetchError(f"{label}: response has no report table")
    rows = table_rows(table)
    return (fields, rows) if rows else None


def parse_price(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "otc")
    if report is None:
        raise NoDataPublishedError
    check_response_date(body, run_date, "otc")
    fields, rows = report
    columns = BarColumns(
        code=column_index(fields, "代號"),
        open=column_index(fields, "開盤"),
        high=column_index(fields, "最高"),
        low=column_index(fields, "最低"),
        close=column_index(fields, "收盤"),
        volume=column_index(fields, "成交股數"),
        turnover=column_index(fields, "成交金額(元)"),
    )
    return bars_from_rows(rows, run_date, columns)


def parse_all_session_volume(body: JsonObject, run_date: date_) -> list[Row]:
    """`{symbol, volume, turnover}` of every row of the dailyQuotes report (warrants included: the
    caller keeps only the symbols of the otc report). Every table is read; the first row of a
    code wins."""
    stat = clean_cell(body.get("stat", ""))
    if stat.lower() != "ok":
        raise ExchangeFetchError(f"dailyQuotes: unexpected stat {stat[:40]!r}")
    check_response_date(body, run_date, "dailyQuotes")
    totals: list[Row] = []
    seen: set[str] = set()
    for raw in as_list(body.get("tables")) or []:
        table = as_object(raw)
        fields = as_list(table.get("fields")) if table else None
        rows = table_rows(table) if table and fields else []
        if not fields or not rows:
            continue
        code = column_index(fields, "代號")
        shares = column_index(fields, "成交股數")
        amount = column_index(fields, "成交金額(元)")
        for row in rows:
            symbol = clean_cell(cell(row, code))
            volume, turnover = to_count(cell(row, shares)), to_count(cell(row, amount))
            if symbol in seen or not is_symbol(symbol) or volume is None or turnover is None:
                continue
            seen.add(symbol)
            totals.append({"symbol": symbol, "volume": volume, "turnover": turnover})
    if not totals:
        raise ExchangeFetchError("dailyQuotes: report has no rows")
    return totals


def with_all_session_volume(bars: list[Row], totals: list[Row]) -> list[Row]:
    """`bars` (round-lot volume/turnover) with the all-session figures of the same symbols. A bar
    without a total, or a total below its own round-lot figure (impossible if the two reports
    describe the same day), fails the whole merge: partial volume is never passed off as whole."""
    by_symbol = {str(t["symbol"]): t for t in totals}
    merged: list[Row] = []
    missing: list[str] = []
    for bar in bars:
        symbol = str(bar["symbol"])
        total = by_symbol.get(symbol)
        if total is None:
            missing.append(symbol)
            continue
        if _below(total["volume"], bar["volume"]) or _below(total["turnover"], bar["turnover"]):
            raise ExchangeFetchError(f"dailyQuotes: {symbol} total is below its round-lot figure")
        merged.append({**bar, "volume": total["volume"], "turnover": total["turnover"]})
    if missing:
        raise ExchangeFetchError(
            f"dailyQuotes: no row for {len(missing)} of {len(bars)} symbols (e.g. {missing[0]})"
        )
    return merged


def _below(total: object, round_lot: object) -> bool:
    return not isinstance(total, int) or not isinstance(round_lot, int) or total < round_lot


def parse_institutional(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "dailyTrade")
    if report is None:
        raise NoDataPublishedError
    check_response_date(body, run_date, "dailyTrade")
    fields, rows = report
    if len(fields) != _INSTITUTIONAL_COLUMNS:
        raise ExchangeFetchError("dailyTrade: unexpected column layout")
    columns = {investor: (at, at + 1) for investor, at in _INVESTOR_BUY_COLUMN.items()}
    return flows_from_rows(rows, run_date, code=0, columns=columns, report="dailyTrade")


def parse_margin(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "balance")
    if report is None:
        raise NoDataPublishedError
    check_response_date(body, run_date, "balance")
    fields, rows = report
    at = {name: column_index(fields, name) for name in _MARGIN_HEADERS}
    out: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, at["代號"]))
        if not is_symbol(symbol):
            continue

        def count(name: str, source: list[object] = row) -> int:
            return to_count(cell(source, at[name])) or 0

        out.append(
            margin_row(
                symbol,
                run_date,
                (
                    [
                        count(n)
                        for n in ("資買", "資賣", "現償", "前資餘額(張)", "資餘額", "資限額")
                    ],
                    # TPEx lists 券賣 before 券買: buy (cover) first, as the margin block does
                    [
                        count(n)
                        for n in ("券買", "券賣", "券償", "前券餘額(張)", "券餘額", "券限額")
                    ],
                ),
                offset=count("資券相抵(張)"),
                note=clean_cell(cell(row, at["備註"])) or None,
            )
        )
    return out


def parse_valuation(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "peQryDate")
    if report is None:
        raise NoDataPublishedError
    check_response_date(body, run_date, "peQryDate")
    fields, rows = report
    columns = ValuationColumns(
        code=column_index(fields, "股票代號"),
        dividend_yield=column_index(fields, "殖利率(%)"),
        per=column_index(fields, "本益比"),
        pbr=column_index(fields, "股價淨值比"),
    )
    return valuations_from_rows(rows, run_date, columns)


def _query(**fixed: str) -> Callable[[date_], dict[str, str]]:
    def build(run_date: date_) -> dict[str, str]:
        return {**fixed, "date": run_date.strftime("%Y/%m/%d"), "id": "", "response": "json"}

    return build


SUPPLEMENTS: Final[dict[Capability, Supplement]] = {
    Capability.PRICE_EOD: Supplement(
        Endpoint(
            "dailyQuotes",
            f"{_BASE}/afterTrading/dailyQuotes",
            _query(type="EW"),
            parse_all_session_volume,
        ),
        with_all_session_volume,
    )
}

ENDPOINTS: Final[dict[Capability, Endpoint]] = {
    Capability.PRICE_EOD: Endpoint(
        "otc", f"{_BASE}/afterTrading/otc", _query(type="EW"), parse_price
    ),
    Capability.INSTITUTIONAL_FLOW: Endpoint(
        "dailyTrade",
        f"{_BASE}/insti/dailyTrade",
        _query(type="Daily", sect="EW"),
        parse_institutional,
    ),
    Capability.MARGIN_SHORT: Endpoint("balance", f"{_BASE}/margin/balance", _query(), parse_margin),
    Capability.VALUATION_RATIOS: Endpoint(
        "peQryDate", f"{_BASE}/afterTrading/peQryDate", _query(), parse_valuation
    ),
}
