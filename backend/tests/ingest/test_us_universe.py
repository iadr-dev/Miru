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
from ingest.us_universe import US_SYMBOL_UNIVERSE_DATASET, job_us_symbol_universe, latest_us_symbols
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
