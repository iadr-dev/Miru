"""Holders/options capability domain — `options_chain`, `institutional_holders`,
`insider_activity`, `fund_holdings`. These are yfinance-native capabilities with no
FinMind/gov equivalent, served only for non-TW markets.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import anyio.to_thread
import pandas as pd
import structlog

from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._blocking import _fetch_option_chain_blocking
from ._shared import _as_date, _as_float, _as_int, _as_str, _dt_iso, _get, _result

logger = structlog.get_logger(__name__)


async def _options_chain(
    sym: Symbol,
    yahoo_symbol: str,
    frames: dict[str, Any],
    params: dict[str, object],
) -> SourceResult:
    expiries = cast("Sequence[str]", frames.get("options") or ())
    if not expiries:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no listed option expiries"),
        )
    requested_expiry = params.get("expiry")
    expiry = str(requested_expiry) if requested_expiry else expiries[0]
    if expiry not in expiries:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail=f"expiry {expiry} not listed for this symbol"
            ),
        )
    try:
        chain = await anyio.to_thread.run_sync(_fetch_option_chain_blocking, yahoo_symbol, expiry)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_option_chain_failed", symbol=yahoo_symbol, error=str(exc))
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc)),
        )
    rows: list[dict[str, object]] = []
    for side, frame in (("call", chain.calls), ("put", chain.puts)):
        if not isinstance(frame, pd.DataFrame):
            continue
        for _, row in frame.iterrows():
            rows.append(
                {
                    "symbol": sym.id,
                    "market": sym.market.value,
                    "expiry": expiry,
                    "contract_symbol": str(_get(row, "contractSymbol", "")),
                    "side": side,
                    "strike": _as_float(_get(row, "strike")) or 0.0,
                    "last_price": _as_float(_get(row, "lastPrice")),
                    "bid": _as_float(_get(row, "bid")),
                    "ask": _as_float(_get(row, "ask")),
                    "volume": _as_int(_get(row, "volume")),
                    "open_interest": _as_int(_get(row, "openInterest")),
                    "implied_volatility": _as_float(_get(row, "impliedVolatility")),
                    "in_the_money": bool(_get(row, "inTheMoney", False)),
                }
            )
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="empty option chain"),
        )
    return _result(
        payload={"contracts": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _institutional_holders(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    rows: list[dict[str, object]] = []
    for holder_type, frame_key in (
        ("institutional", "institutional_holders"),
        ("mutual_fund", "mutualfund_holders"),
    ):
        frame = frames.get(frame_key)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for _, row in frame.iterrows():
            reported = _as_date(_get(row, "Date Reported"))
            if reported is None:
                continue
            rows.append(
                {
                    "symbol": sym.id,
                    "market": sym.market.value,
                    "holder_type": holder_type,
                    "holder_name": str(_get(row, "Holder", "")),
                    "date_reported": reported.isoformat(),
                    "shares": _as_int(_get(row, "Shares")),
                    "value": _as_float(_get(row, "Value")),
                    "pct_held": _as_float(_get(row, "pctHeld")),
                    "pct_change": _as_float(_get(row, "pctChange")),
                }
            )
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no institutional/mutual-fund holders"
            ),
        )
    return _result(
        payload={"holders": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _insider_activity(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    frame = frames.get("insider_transactions")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE,
                detail="no insider transactions (US Form-4-style "
                "disclosure; empty is expected outside the US)",
            ),
        )
    rows: list[dict[str, object]] = [
        {
            "symbol": sym.id,
            "market": sym.market.value,
            "insider_name": str(_get(row, "Insider", "")),
            "position": _as_str(_get(row, "Position")),
            "transaction": _as_str(_get(row, "Text")),
            "shares": _as_int(_get(row, "Shares")),
            "value": _as_float(_get(row, "Value")),
            "start_date": _dt_iso(_as_date(_get(row, "Start Date"))),
            "ownership": _as_str(_get(row, "Ownership")),
        }
        for _, row in frame.iterrows()
    ]
    return _result(
        payload={"transactions": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _fund_holdings(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    funds_data = frames.get("funds_data")
    if funds_data is None:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="symbol is not a fund/ETF (no funds data)"
            ),
        )
    # `FundsData` is lazy: the property access above (`_safe_funds_data`) succeeding does NOT
    # mean this symbol is a real fund — `.top_holdings`/`.sector_weightings` trigger their own
    # later fetch-and-parse on first access, which raises `YFDataException` for an ordinary
    # equity (confirmed live: crashed `GET /funds/AAPL/holdings` with an unhandled 500 — AAPL is
    # a stock, not a fund, so `funds_data` resolves non-None but has nothing to parse). Same
    # "expected outcome, not an error" contract `_safe_funds_data` already establishes.
    try:
        top_holdings = getattr(funds_data, "top_holdings", None)
        sector_weightings = getattr(funds_data, "sector_weightings", None)
    except Exception:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="symbol is not a fund/ETF (no funds data)"
            ),
        )
    holdings: list[dict[str, object]] = []
    if isinstance(top_holdings, pd.DataFrame) and not top_holdings.empty:
        holdings = [
            {
                "symbol": sym.id,
                "market": sym.market.value,
                "holding_symbol": str(idx),
                "holding_name": str(_get(row, "Name", "")),
                "weight": _as_float(_get(row, "Holding Percent")) or 0.0,
            }
            for idx, row in top_holdings.iterrows()
        ]
    weightings: list[dict[str, object]] = []
    if isinstance(sector_weightings, dict):
        weightings = [
            {
                "symbol": sym.id,
                "market": sym.market.value,
                "sector": sector,
                "weight": _as_float(weight) or 0.0,
            }
            for sector, weight in cast("dict[str, object]", sector_weightings).items()
        ]
    if not holdings and not weightings:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="fund has no holdings data"),
        )
    return _result(
        payload={"holdings": holdings, "sector_weightings": weightings},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


__all__ = ["_fund_holdings", "_insider_activity", "_institutional_holders", "_options_chain"]
