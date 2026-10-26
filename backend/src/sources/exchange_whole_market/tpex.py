"""TPEx (OTC market) endpoints and parsers - the `www.tpex.org.tw/www/zh-tw` daily reports.

Same contract as `twse.py`: each parser produces the FinMind row shape. Requests carry a Gregorian
`YYYY/MM/DD` date; the answer is `{"stat": "ok", "date": "YYYYMMDD", "tables": [{...}]}` with the
report in `tables[0]` (its own `date` is ROC, which is never used: the root date is checked).

- PRICE_EOD: afterTrading/otc (EW), every security except warrants and 定價 trading.
- INSTITUTIONAL_FLOW: insti/dailyTrade (EW); 24 unnamed columns, read by position.
- MARGIN_SHORT: margin/balance; note the order, 券賣 comes before 券買.
- VALUATION_RATIOS: afterTrading/peQryDate; a `-` PER is 0.0, as in FinMind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_
from typing import Final

import structlog

from schemas.enums import Capability

from ._common import (
    TAIWAN_MARKET,
    Endpoint,
    ExchangeFetchError,
    JsonObject,
    Row,
    as_list,
    as_object,
    cell,
    check_response_date,
    clean_cell,
    column_index,
    is_symbol,
    table_rows,
    to_count,
    to_number,
)

logger = structlog.get_logger(__name__)

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
        return []
    check_response_date(body, run_date, "otc")
    fields, rows = report
    code = column_index(fields, "代號")
    close = column_index(fields, "收盤")
    open_ = column_index(fields, "開盤")
    high = column_index(fields, "最高")
    low = column_index(fields, "最低")
    volume = column_index(fields, "成交股數")
    turnover = column_index(fields, "成交金額(元)")
    bars: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        prices = [to_number(cell(row, i)) for i in (open_, high, low, close)]
        shares = to_count(cell(row, volume))
        if not is_symbol(symbol) or any(p is None or p <= 0 for p in prices) or shares is None:
            continue  # did not trade: the prices print as '---'
        bars.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": shares,
                "turnover": to_count(cell(row, turnover)),
            }
        )
    return bars


def parse_institutional(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "dailyTrade")
    if report is None:
        return []
    check_response_date(body, run_date, "dailyTrade")
    fields, rows = report
    if len(fields) != _INSTITUTIONAL_COLUMNS:
        raise ExchangeFetchError("dailyTrade: unexpected column layout")
    flows: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, 0))
        if not is_symbol(symbol):
            continue
        counts = {
            investor: (to_count(cell(row, at)), to_count(cell(row, at + 1)))
            for investor, at in _INVESTOR_BUY_COLUMN.items()
        }
        if any(b is None or s is None for b, s in counts.values()):
            logger.warning("exchange_row_dropped", report="dailyTrade", symbol=symbol)
            continue
        flows.extend(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "investor": investor,
                "buy": buy,
                "sell": sell,
            }
            for investor, (buy, sell) in counts.items()
        )
    return flows


def parse_margin(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "balance")
    if report is None:
        return []
    check_response_date(body, run_date, "balance")
    fields, rows = report
    at = {
        name: column_index(fields, name)
        for name in (
            "代號", "資買", "資賣", "現償", "前資餘額(張)", "資餘額", "資限額",
            "券買", "券賣", "券償", "前券餘額(張)", "券餘額", "券限額", "資券相抵(張)", "備註",
        )
    }  # fmt: skip
    out: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, at["代號"]))
        if not is_symbol(symbol):
            continue

        def count(name: str, source: list[object] = row) -> int:
            return to_count(cell(source, at[name])) or 0

        out.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "margin_purchase_buy": count("資買"),
                "margin_purchase_sell": count("資賣"),
                "margin_purchase_cash_repayment": count("現償"),
                "margin_purchase_yesterday_balance": count("前資餘額(張)"),
                "margin_purchase_today_balance": count("資餘額"),
                "margin_purchase_limit": count("資限額"),
                "short_sale_buy": count("券買"),
                "short_sale_sell": count("券賣"),
                "short_sale_cash_repayment": count("券償"),
                "short_sale_yesterday_balance": count("前券餘額(張)"),
                "short_sale_today_balance": count("券餘額"),
                "short_sale_limit": count("券限額"),
                "offset_loan_and_short": count("資券相抵(張)"),
                "note": clean_cell(cell(row, at["備註"])) or None,
            }
        )
    return out


def parse_valuation(body: JsonObject, run_date: date_) -> list[Row]:
    report = _first_table(body, "peQryDate")
    if report is None:
        return []
    check_response_date(body, run_date, "peQryDate")
    fields, rows = report
    code = column_index(fields, "股票代號")
    per = column_index(fields, "本益比")
    yield_ = column_index(fields, "殖利率(%)")
    pbr = column_index(fields, "股價淨值比")
    ratios: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        if not is_symbol(symbol):
            continue
        ratios.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "dividend_yield": to_number(cell(row, yield_)) or 0.0,
                "per": to_number(cell(row, per)) or 0.0,
                "pbr": to_number(cell(row, pbr)) or 0.0,
            }
        )
    return ratios


def _query(**fixed: str) -> Callable[[date_], dict[str, str]]:
    def build(run_date: date_) -> dict[str, str]:
        return {**fixed, "date": run_date.strftime("%Y/%m/%d"), "id": "", "response": "json"}

    return build


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
