"""Unit coverage for `analytics/screener/engine.py`'s US branch — specifically that
`ScreenerSurvivor.display` is populated from Yahoo's already-returned quote fields, not a second
per-symbol fetch. Mocks `run_equity_screener` the same way `tests/sources/yfinance/test_screener.py`
mocks `anyio.to_thread.run_sync` for the underlying `yf.screen()` call — no live network."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from analytics.screener.engine import (
    RunScreenerInput,
    _etf_trailing_returns_by_symbol,
    run_screener,
)
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from schemas.screener import ScreenerAssetKind, ScreenerHit, ScreenerResult


async def test_us_survivor_display_carries_yahoo_quote_fields(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    hit = ScreenerHit(
        symbol="MSFT",
        name="Microsoft Corporation",
        exchange="NasdaqGS",
        region="us_market",
        matched_fields={
            "peratio.lasttwelvemonths": 32.5,
            "regularMarketPrice": 428.15,
            "regularMarketChangePercent": 1.15,
            "regularMarketVolume": 21_840_000.0,
            "marketCap": 3_180_000_000_000.0,
            "sector": "Technology",
        },
        rank=1,
        score=None,
    )
    fake_result = ScreenerResult(
        asset_kind=ScreenerAssetKind.EQUITY, total_matches=1, hits=(hit,), detail="ok"
    )
    monkeypatch.setattr(
        "analytics.screener.engine.run_equity_screener", AsyncMock(return_value=fake_result)
    )

    output = await run_screener(
        RunScreenerInput(
            market="US",
            conditions=[{"factor": "pe_ratio", "op": "lt", "value": 40}],
            match="AND",
        ),
        parquet_store=ParquetStore(root=tmp_path),
        tag_reader=ParquetMarketDataReader(root=tmp_path),
        session_factory=None,  # type: ignore[arg-type]  # unused on the US branch
    )

    assert len(output.survivors) == 1
    survivor = output.survivors[0]
    assert survivor.symbol == "MSFT"
    assert survivor.display["regularMarketPrice"] == 428.15
    assert survivor.display["marketCap"] == 3_180_000_000_000.0
    assert survivor.display["sector"] == "Technology"


async def test_us_survivor_display_empty_when_field_absent(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yahoo's screener response only includes fields it actually has for that symbol — a
    missing field is honestly absent from `display`, never backfilled with a fabricated value."""
    hit = ScreenerHit(
        symbol="PENNY",
        name=None,
        exchange=None,
        region=None,
        matched_fields={"peratio.lasttwelvemonths": 5.0},
        rank=1,
        score=None,
    )
    fake_result = ScreenerResult(
        asset_kind=ScreenerAssetKind.EQUITY, total_matches=1, hits=(hit,), detail="ok"
    )
    monkeypatch.setattr(
        "analytics.screener.engine.run_equity_screener", AsyncMock(return_value=fake_result)
    )

    output = await run_screener(
        RunScreenerInput(
            market="US",
            conditions=[{"factor": "pe_ratio", "op": "lt", "value": 40}],
            match="AND",
        ),
        parquet_store=ParquetStore(root=tmp_path),
        tag_reader=ParquetMarketDataReader(root=tmp_path),
        session_factory=None,  # type: ignore[arg-type]
    )

    assert "marketCap" not in output.survivors[0].display


def test_etf_return_1y_scopes_read_range_to_etf_symbols(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the hung-request bug found in manual testing: an earlier version of
    this function called `store.read_range(..., symbols=None)` — the whole-market unfiltered form
    `ingest/store.py::read_range`'s own docstring measures at ~445K rows for a 20-day/one-market
    window, materialized as Python dicts, on every ETF screener request. Asserts the fix: every
    `read_range` call this function makes passes the caller's `etf_symbols` set as the `symbols=`
    kwarg, so DuckDB filters at the SQL layer instead."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    original = ParquetStore.read_range

    def _spy(self: ParquetStore, *args: object, **kwargs: object) -> list[dict[str, object]]:
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ParquetStore, "read_range", _spy)

    store = ParquetStore(root=tmp_path)
    etf_symbols = {"00900", "0050"}
    _etf_trailing_returns_by_symbol(store, "TW", date(2024, 1, 2), etf_symbols)

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs.get("symbols") is not None
    assert set(kwargs["symbols"]) == etf_symbols  # type: ignore[arg-type]
