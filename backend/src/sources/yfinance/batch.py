"""Batch multi-symbol OHLCV download — the ingest-layer entry point.

Analogous to `FinMindAdapter.fetch_whole_market`. Not part of `SourceAdapter` — deliberately
not reachable through `dataaccess/router.py`. A future weekly ingest job wires this behind a
cron trigger and writes the result to Parquet; the adapter's `fetch()` stays strictly
per-symbol, this is the one legitimate batch path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anyio.to_thread

from ._shared import _init_tz_cache


async def batch_download(
    symbols: Sequence[str],
    *,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
) -> Any:  # returns a raw yfinance/pandas MultiIndex DataFrame, the ingest
    # layer's own row-mapper is responsible for reshaping it, not this adapter
    """Batch multi-symbol OHLCV download (`yfinance.download([...], group_by='ticker')`).

    Runs the blocking download call in a threadpool, same as every other yfinance call in
    this package.
    """

    def _blocking() -> Any:
        import yfinance as yf  # noqa: PLC0415

        _init_tz_cache()
        return yf.download(
            list(symbols), start=start, end=end, interval=interval, group_by="ticker"
        )

    return await anyio.to_thread.run_sync(_blocking)


__all__ = ["batch_download"]
