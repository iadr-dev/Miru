"""`etf_trailing_returns` capability domain — see `EtfTrailingReturns`'s own docstring
(`schemas/capabilities.py`) for why this reads a dedicated split/dividend-adjusted history fetch
rather than yfinance's own `.info` return fields or this codebase's ingested (unadjusted)
`price_eod` bars.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import anyio.to_thread

from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._blocking import _fetch_adjusted_history_blocking
from ._shared import _as_float, _result, _yahoo_symbol

_HISTORY_PERIOD = "5y"
_MIN_USABLE_CLOSES = 2

# (output field name, trading-day lookback) — trading days, not calendar days, since the
# lookback walks back a fixed COUNT of rows in the already-fetched daily-bar series, not a
# date arithmetic window. ~21 trading days/month, ~252/year is this codebase's own convention
# (matches `analytics/indicators.py`'s MA/momentum window sizing elsewhere).
_HORIZONS: tuple[tuple[str, int], ...] = (
    ("return_1m_pct", 21),
    ("return_1q_pct", 63),
    ("return_6m_pct", 126),
    ("return_1y_pct", 252),
    ("return_3y_pct", 756),
    ("return_5y_pct", 1260),
)


def _horizon_return(closes: list[float], lookback_trading_days: int) -> float | None:
    """`None` (never fabricated) when the series doesn't reach back far enough for this
    horizon — e.g. a fund listed 8 months ago has no real 1-year return yet."""
    if len(closes) <= lookback_trading_days:
        return None
    start_close = closes[-1 - lookback_trading_days]
    end_close = closes[-1]
    if start_close <= 0:
        return None
    return (end_close / start_close - 1) * 100.0


async def _etf_trailing_returns(sym: Symbol) -> SourceResult:
    yahoo_symbol = _yahoo_symbol(sym)
    try:
        history = await anyio.to_thread.run_sync(
            _fetch_adjusted_history_blocking, yahoo_symbol, _HISTORY_PERIOD
        )
    except Exception as exc:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc)),
        )
    if history.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no price history returned"),
        )
    raw_closes: list[object] = cast("list[object]", history["Close"].tolist())
    closes = [c for c in (_as_float(v) for v in raw_closes) if c is not None]
    if len(closes) < _MIN_USABLE_CLOSES:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="fewer than 2 usable closes"),
        )
    returns = {field_name: _horizon_return(closes, lookback) for field_name, lookback in _HORIZONS}
    row = {
        "symbol": sym.id,
        "market": sym.market.value,
        "as_of": date.today().isoformat(),
        **returns,
    }
    return _result(
        payload={"returns": row}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


__all__ = ["_etf_trailing_returns"]
