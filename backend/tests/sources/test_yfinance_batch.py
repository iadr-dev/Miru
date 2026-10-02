"""`batch_download` chunking: a whole-market symbol universe beyond `_CHUNK_SIZE` must split
into multiple `yf.download` calls with a pause between them, never one call over the whole
universe — regression test for the real bug found on the live US backfill, where one call over
the ~1,130-symbol universe flooded Yahoo's rate limiter regardless of any pacing between dates.
"""

from __future__ import annotations

from collections.abc import Callable

import anyio.to_thread
import pandas as pd
import pytest
import yfinance as yf

import sources.yfinance.batch as batch_module
from sources.yfinance.batch import _CHUNK_SIZE, batch_download


def _frame_for(symbols: list[str]) -> pd.DataFrame:
    idx = pd.MultiIndex.from_product([symbols, ["Open", "High", "Low", "Close", "Volume"]])
    return pd.DataFrame([[1.0] * len(idx)], columns=idx, index=pd.to_datetime(["2024-01-02"]))


async def test_batch_download_chunks_a_universe_larger_than_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_run_sync(fn: object, chunk: list[str]) -> pd.DataFrame:
        calls.append(chunk)
        return _frame_for(chunk)

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    symbols = [f"SYM{i}" for i in range(_CHUNK_SIZE + 10)]
    await batch_download(symbols, start="2024-01-02", end="2024-01-03")

    assert len(calls) == 2
    assert len(calls[0]) == _CHUNK_SIZE
    assert len(calls[1]) == 10


async def test_batch_download_single_chunk_makes_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_run_sync(fn: object, chunk: list[str]) -> pd.DataFrame:
        calls.append(chunk)
        return _frame_for(chunk)

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    await batch_download(["AAPL", "MSFT"], start="2024-01-02", end="2024-01-03")

    assert len(calls) == 1


async def test_batch_download_throttles_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    async def fake_run_sync(fn: object, chunk: list[str]) -> pd.DataFrame:
        return _frame_for(chunk)

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)
    monkeypatch.setattr(batch_module.asyncio, "sleep", fake_sleep)

    symbols = [f"SYM{i}" for i in range(_CHUNK_SIZE * 2 + 1)]
    await batch_download(symbols, start="2024-01-02", end="2024-01-03")

    # 3 chunks, 2 pauses (never before the first chunk).
    assert len(sleep_calls) == 2


@pytest.mark.parametrize(("flag", "expected"), [(None, True), (False, False)])
async def test_batch_download_forwards_auto_adjust(
    monkeypatch: pytest.MonkeyPatch, flag: bool | None, expected: bool
) -> None:
    """Default stays adjusted (existing callers unchanged); `False` reaches `yf.download` so a
    multi-year backfill can request raw as-traded prices."""
    seen: dict[str, object] = {}

    def fake_download(chunk: list[str], **kwargs: object) -> pd.DataFrame:
        seen.update(kwargs)
        return pd.DataFrame()

    async def fake_run_sync(fn: Callable[[list[str]], object], chunk: list[str]) -> object:
        return fn(chunk)

    monkeypatch.setattr(yf, "download", fake_download)
    monkeypatch.setattr(batch_module, "_init_tz_cache", lambda: None)
    monkeypatch.setattr(batch_module.anyio.to_thread, "run_sync", fake_run_sync)

    kwargs: dict[str, bool] = {} if flag is None else {"auto_adjust": flag}
    await batch_download(["AAPL"], start="2024-01-02", end="2024-01-03", **kwargs)

    assert seen["auto_adjust"] is expected
