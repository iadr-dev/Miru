"""Live index-quote capability — the US ticker-strip's `S&P500`/`Dow Jones`/`Nasdaq`/`SOX`/`VIX`/
`US 10Y` items. Confirmed live: `yf.Ticker("^GSPC").fast_info` (and `^DJI`/`^IXIC`/`^SOX`/`^VIX`/
`^TNX`) return real `lastPrice`/`previousClose` quotes — these are index tickers, never part of
our own ingested `us_price_eod` equity universe, so there is no whole-market path for them; this
is the same bounded, per-request live-fetch shape `discovery.py`'s `symbol_search`/
`economic_calendar` already establish as accepted (one symbol per call, caller-bounded, never a
full-market scan).

Not part of `SourceAdapter`/`fetch()` — an index quote has no `(capability, symbol)` entry in
this codebase's `Capability` enum, and adding one just for 6 fixed tickers would be more
indirection than the direct, small module-level function this is instead — the same "carve-out,
not a capability" reasoning `screener.py`/`batch.py` already establish for their own non-router
shapes in this package.
"""

from __future__ import annotations

import anyio.to_thread
import structlog

from ._blocking import _fetch_fast_info_blocking
from ._shared import _as_float, _get

logger = structlog.get_logger(__name__)


async def fetch_index_quote(yahoo_symbol: str) -> tuple[float | None, float | None]:
    """`(last_price, previous_close)` for one index ticker, or `(None, None)` on any failure —
    never raises, mirrors every other yfinance carve-out's honest-degrade contract."""
    try:
        fast_info = await anyio.to_thread.run_sync(_fetch_fast_info_blocking, yahoo_symbol)
    except Exception as exc:  # never raise out of a quote fetch — degrade honestly
        logger.warning("yfinance_index_quote_failed", symbol=yahoo_symbol, error=str(exc))
        return None, None
    return _as_float(_get(fast_info, "lastPrice")), _as_float(_get(fast_info, "previousClose"))


__all__ = ["fetch_index_quote"]
