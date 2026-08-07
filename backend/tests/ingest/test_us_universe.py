"""US symbol-universe discovery tests: bounded screener query, leader gate, empty-result
handling, and the `latest_us_symbols` read-back helper."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import anyio.to_thread
import pytest

from config.settings import Settings
from ingest.context import IngestContext
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from ingest.us_universe import (
    _MARKET_CAP_BANDS,
    US_SYMBOL_UNIVERSE_DATASET,
    discover_us_symbols,
    job_us_symbol_universe,
    latest_us_symbols,
)
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter


def _ctx(tmp_path: object, *, leader: object = None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _patch_screen(monkeypatch: pytest.MonkeyPatch, response: dict[str, object]) -> None:
    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        assert getattr(fn, "__name__", "") == "_run_screener_blocking"
        return response

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


def _screen_response() -> dict[str, object]:
    return {
        "total": 2,
        "quotes": [
            {
                "symbol": "AAPL",
                "longName": "Apple Inc.",
                "fullExchangeName": "NasdaqGS",
                "market": "us_market",
            },
            {
                "symbol": "MSFT",
                "longName": "Microsoft Corporation",
                "fullExchangeName": "NasdaqGS",
                "market": "us_market",
            },
        ],
    }


async def test_job_writes_the_discovered_universe(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_screen(monkeypatch, _screen_response())
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)

    run = await job_us_symbol_universe(run_date, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    assert ctx.store.has_partition(US_SYMBOL_UNIVERSE_DATASET, "US", run_date)
    rows = ctx.store.read_partition(US_SYMBOL_UNIVERSE_DATASET, "US", run_date)
    assert {row["symbol"] for row in rows} == {"AAPL", "MSFT"}


async def test_empty_screener_result_is_a_failed_journaled_run_not_a_crash(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_screen(monkeypatch, {"total": 0, "quotes": []})
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)

    run = await job_us_symbol_universe(run_date, ctx)

    assert run.status is RunStatus.FAILED
    assert run.rows_written == 0
    assert not ctx.store.has_partition(US_SYMBOL_UNIVERSE_DATASET, "US", run_date)


async def test_non_leader_blocks_write_with_no_screener_call(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(fn: object, *args: object) -> object:
        raise AssertionError("a non-leader must not query the screener")

    monkeypatch.setattr(anyio.to_thread, "run_sync", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())
    run_date = date(2024, 1, 2)

    run = await job_us_symbol_universe(run_date, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(US_SYMBOL_UNIVERSE_DATASET, "US", run_date)


async def test_latest_us_symbols_reads_back_the_newest_partition(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_screen(monkeypatch, _screen_response())
    ctx = _ctx(tmp_path)
    older = date(2024, 1, 2)
    newer = date(2024, 2, 1)

    await job_us_symbol_universe(older, ctx)
    await job_us_symbol_universe(newer, ctx)

    symbols = latest_us_symbols(ctx.store)
    assert sorted(symbols) == ["AAPL", "MSFT"]


def test_latest_us_symbols_is_empty_when_never_run(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    assert latest_us_symbols(store) == []


def _quote(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "longName": f"{symbol} Inc.",
        "fullExchangeName": "NasdaqGS",
        "market": "us_market",
    }


async def test_discover_us_symbols_queries_every_band_and_merges_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One real, distinct symbol per band — proves every band in `_MARKET_CAP_BANDS` is
    actually queried (not just the first), and their results are merged into one list."""
    band_symbols = [f"SYM{i}" for i in range(len(_MARKET_CAP_BANDS))]
    call_count = 0

    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        nonlocal call_count
        assert getattr(fn, "__name__", "") == "_run_screener_blocking"
        symbol = band_symbols[call_count]
        call_count += 1
        return {"total": 1, "quotes": [_quote(symbol)]}

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    rows = await discover_us_symbols()

    assert call_count == len(_MARKET_CAP_BANDS)
    assert {row["symbol"] for row in rows} == set(band_symbols)


async def test_discover_us_symbols_deduplicates_across_bands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive dedup: even if two bands somehow return the same symbol, it appears once."""

    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        return {"total": 1, "quotes": [_quote("AAPL")]}

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    rows = await discover_us_symbols()

    assert [row["symbol"] for row in rows] == ["AAPL"]


async def test_discover_us_symbols_tolerates_one_empty_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One band returning nothing (e.g. a transient upstream hiccup for that query) must not
    fail the whole discovery run — the other bands' results still land."""
    call_count = 0

    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"total": 0, "quotes": []}
        return {"total": 1, "quotes": [_quote(f"SYM{call_count}")]}

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    rows = await discover_us_symbols()

    assert call_count == len(_MARKET_CAP_BANDS)
    assert len(rows) == len(_MARKET_CAP_BANDS) - 1
