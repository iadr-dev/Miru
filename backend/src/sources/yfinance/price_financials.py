"""Non-TW price/financials capability domain — `price_eod`, `financials`/`balance_sheet`/
`cash_flow_statement` (one shared handler parameterized by statement type), `dividends`, and
`split_price`. These are the shared capabilities other adapters also serve for TW; here
yfinance is the primary source for the non-TW markets.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._shared import _as_date, _as_float, _as_int, _dt_iso, _get, _result


def _price_eod(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    history = frames.get("history")
    if not isinstance(history, pd.DataFrame) or history.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no price history returned"),
        )
    bars = [
        {
            "symbol": sym.id,
            "market": sym.market.value,
            "trade_date": _dt_iso(_as_date(cast("object", idx))),
            "open": _as_float(_get(row, "Open")),
            "high": _as_float(_get(row, "High")),
            "low": _as_float(_get(row, "Low")),
            "close": _as_float(_get(row, "Close")),
            "volume": _as_int(_get(row, "Volume")) or 0,
            "turnover": None,
        }
        for idx, row in history.iterrows()
    ]
    return _result(payload={"bars": bars}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


def _financial_statement(  # noqa: PLR0913 — one keyword-only arg per optional yfinance frame
    # frequency (annual is positional/required, quarterly/TTM are optional per statement type —
    # see the balance-sheet call site, which omits `ttm_frame_key` since no such frame exists);
    # splitting this into a request object would only move the same argument count one level out.
    sym: Symbol,
    frames: dict[str, Any],
    frame_key: str,
    statement_type: str,
    *,
    quarterly_frame_key: str | None = None,
    ttm_frame_key: str | None = None,
) -> SourceResult:
    """`frame_key`'s frame is tagged `period_type="annual"`; `quarterly_frame_key`/
    `ttm_frame_key` (when given — balance sheet has no TTM concept, it's a point-in-time
    snapshot, not a flow statement) are tagged `"quarterly"`/`"ttm"` and merged into the same
    line list. yfinance genuinely reports these as three distinct frames (unlike FinMind's single
    quarterly-only TW frame, where `FinancialStatementLine.period_type` just defaults), so a US
    caller gets the real annual-vs-quarterly-vs-TTM distinction rather than one frequency."""
    lines: list[dict[str, object]] = []
    for key, period_type in (
        (frame_key, "annual"),
        (quarterly_frame_key, "quarterly"),
        (ttm_frame_key, "ttm"),
    ):
        if key is None:
            continue
        lines.extend(_financial_statement_lines(sym, frames.get(key), statement_type, period_type))
    if not lines:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail=f"no {statement_type} line items"
            ),
        )
    return _result(
        payload={"lines": lines}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _financial_statement_lines(
    sym: Symbol, frame: object, statement_type: str, period_type: str
) -> list[dict[str, object]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    lines: list[dict[str, object]] = []
    for item, row in frame.iterrows():
        for period_end, value in row.items():
            numeric = _as_float(cast("object", value))
            if numeric is None:
                continue
            period_end_date = _as_date(cast("object", period_end))
            lines.append(
                {
                    "symbol": sym.id,
                    "market": sym.market.value,
                    "period_end": _dt_iso(period_end_date),
                    "statement_type": statement_type,
                    "period_type": period_type,
                    "item": str(item),
                    "origin_name": str(item),
                    "value": numeric,
                }
            )
    return lines


def _dividends(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    series = frames.get("dividends")
    if not isinstance(series, pd.Series) or series.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no dividend history"),
        )
    events = []
    for raw_idx, amount in series.items():
        ex_date = _as_date(cast("object", raw_idx))
        events.append(
            {
                "symbol": sym.id,
                "market": sym.market.value,
                "announced_date": _dt_iso(ex_date),
                "fiscal_year": str(ex_date.year) if ex_date else "",
                "cash_dividend": _as_float(cast("object", amount)),
                "stock_dividend": None,
                "cash_ex_dividend_date": _dt_iso(ex_date),
                "stock_ex_dividend_date": None,
            }
        )
    return _result(
        payload={"events": events}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _split_price(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    series = frames.get("splits")
    if not isinstance(series, pd.Series) or series.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no split history"),
        )
    rows = []
    for raw_idx, ratio in series.items():
        ratio_value = _as_float(cast("object", ratio)) or 0.0
        rows.append(
            {
                "symbol": sym.id,
                "market": sym.market.value,
                "trade_date": _dt_iso(_as_date(cast("object", raw_idx))),
                "split_type": f"{ratio_value:g}-for-1",
                "before_price": None,
                "after_price": None,
                "max_price": None,
                "min_price": None,
                "open_price": None,
                "split_ratio": ratio_value,
            }
        )
    return _result(payload={"rows": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


__all__ = ["_dividends", "_financial_statement", "_price_eod", "_split_price"]
