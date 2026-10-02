"""Batch multi-symbol OHLCV download — the ingest-layer entry point.

Analogous to `FinMindAdapter.fetch_whole_market`. Not part of `SourceAdapter` — deliberately
not reachable through `dataaccess/router.py`. A future weekly ingest job wires this behind a
cron trigger and writes the result to Parquet; the adapter's `fetch()` stays strictly
per-symbol, this is the one legitimate batch path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import anyio.to_thread

from ._shared import _init_tz_cache

# `yf.download([...])`'s own internal per-symbol threading (`threads=True` by default) fires
# near-simultaneous requests for every symbol in the call — fine at ~250 symbols (confirmed by
# an isolated live test), but the whole-market universe now runs ~1,000-1,500 symbols
# (market-cap-band pagination), and one call at that size floods Yahoo's rate limiter
# immediately regardless of any pacing *between* calls. Chunking the universe and pausing
# between chunks keeps each individual `yf.download` call at the size already proven to work.
_CHUNK_SIZE = 250
_CHUNK_THROTTLE_SECONDS = 5.0


async def batch_download(
    symbols: Sequence[str],
    *,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = True,
) -> Any:  # returns a raw yfinance/pandas MultiIndex DataFrame, the ingest
    # layer's own row-mapper is responsible for reshaping it, not this adapter
    """Batch multi-symbol OHLCV download (`yfinance.download([...], group_by='ticker')`),
    chunked at `_CHUNK_SIZE` with a pause between chunks — see the module-level comment for why
    a universe beyond ~250 symbols can't be one `yf.download` call.

    Runs each chunk's blocking download call in a threadpool, same as every other yfinance call
    in this package.

    `auto_adjust=False` returns the raw as-traded OHLC. A multi-year range with the default
    `True` is back-adjusted for dividends and can go negative on thin, high-yield OTC tickers,
    so a historical backfill that must match the single-day rows the daily job stores passes
    `False`.
    """
    symbol_list = list(symbols)
    chunks = [
        symbol_list[i : i + _CHUNK_SIZE] for i in range(0, len(symbol_list), _CHUNK_SIZE)
    ] or [[]]

    def _blocking(chunk: list[str]) -> Any:
        import yfinance as yf  # noqa: PLC0415

        _init_tz_cache()
        # `threads=False`: yf.download's own internal per-symbol thread pool (the default) is
        # spawned fresh on every call and, across many sequential chunked calls in one
        # long-running backfill, accumulates OS threads faster than they're reaped — confirmed
        # live as a real hang ("can't start new thread", the process left sleeping on 40+
        # threads with 0% CPU, never recovering on its own). One `anyio.to_thread.run_sync`
        # worker per chunk is already the concurrency this call gets; no need for a second,
        # nested thread pool underneath it.
        return yf.download(
            chunk,
            start=start,
            end=end,
            interval=interval,
            group_by="ticker",
            threads=False,
            auto_adjust=auto_adjust,
        )

    frames: list[Any] = []
    for i, chunk in enumerate(chunks):
        if i:
            await asyncio.sleep(_CHUNK_THROTTLE_SECONDS)
        frames.append(await anyio.to_thread.run_sync(_blocking, chunk))

    if len(frames) == 1:
        return frames[0]
    import pandas as pd  # noqa: PLC0415

    return pd.concat(frames, axis=1)


__all__ = ["batch_download"]
