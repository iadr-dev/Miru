"""US whole-market ingest job tests: `job_us_price_eod` (batch_download row-mapping, calendar/
leader gating, empty-universe skip), `job_us_fundamentals` (per-symbol fan-out, partial-failure
journaling), and `backfill_us_price_range` (gap-fill over a date window)."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import anyio.to_thread
import pandas as pd
import pytest

import sources.yfinance._adapter as yfinance_adapter_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from ingest.us_jobs import (
    US_PRICE_EOD_DATASET,
    backfill_us_price_range,
    job_us_fundamentals,
    job_us_price_eod,
)
from ingest.us_universe import US_SYMBOL_UNIVERSE_DATASET
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.finmind import FinMindAdapter
from sources.yfinance import YFinanceAdapter


def _ctx(tmp_path: object, *, leader: object = None, with_yfinance: bool = True) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        yfinance=YFinanceAdapter() if with_yfinance else None,
    )


def _seed_universe(ctx: IngestContext, run_date: date, symbols: list[str]) -> None:
    rows = [{"symbol": s, "name": s, "exchange": "NasdaqGS"} for s in symbols]
    ctx.store.write_partition(US_SYMBOL_UNIVERSE_DATASET, Market.US.value, run_date, rows)


def _two_symbol_frame(trade_date: date) -> pd.DataFrame:
    idx = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]])
    return pd.DataFrame(
        [[100.0, 101.0, 99.0, 100.5, 1000, 200.0, 202.0, 198.0, 201.0, 2000]],
        columns=idx,
        index=pd.to_datetime([trade_date]),
    )


def _patch_batch_download(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    async def fake_run_sync(fn: object, *args: object) -> pd.DataFrame:
        assert getattr(fn, "__name__", "") == "_blocking"
        return frame

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


async def test_job_us_price_eod_writes_rows_from_batch_download(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)  # a Tuesday
    _seed_universe(ctx, trading_day, ["AAPL", "MSFT"])
    _patch_batch_download(monkeypatch, _two_symbol_frame(trading_day))

    run = await job_us_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    rows = ctx.store.read_partition(US_PRICE_EOD_DATASET, "US", trading_day)
    assert {row["symbol"] for row in rows} == {"AAPL", "MSFT"}
    aapl = next(r for r in rows if r["symbol"] == "AAPL")
    assert aapl["close"] == 100.5


async def test_job_us_price_eod_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)
    _seed_universe(ctx, saturday, ["AAPL"])

    def _boom(fn: object, *args: object) -> object:
        raise AssertionError("must not fetch on a non-trading day")

    monkeypatch.setattr(anyio.to_thread, "run_sync", _boom)

    run = await job_us_price_eod(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(US_PRICE_EOD_DATASET, "US", saturday)


async def test_job_us_price_eod_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, leader=NeverLeader())
    trading_day = date(2024, 1, 2)
    _seed_universe(ctx, trading_day, ["AAPL"])

    def _boom(fn: object, *args: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(anyio.to_thread, "run_sync", _boom)

    run = await job_us_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(US_PRICE_EOD_DATASET, "US", trading_day)


async def test_job_us_price_eod_empty_universe_is_skipped(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)

    def _boom(fn: object, *args: object) -> object:
        raise AssertionError("no universe means nothing to fetch")

    monkeypatch.setattr(anyio.to_thread, "run_sync", _boom)

    run = await job_us_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0


async def test_job_us_price_eod_idempotent_rerun_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)
    _seed_universe(ctx, trading_day, ["AAPL", "MSFT"])
    _patch_batch_download(monkeypatch, _two_symbol_frame(trading_day))

    first = await job_us_price_eod(trading_day, ctx)
    second = await job_us_price_eod(trading_day, ctx)

    assert first.rows_written == second.rows_written
    rows = ctx.store.read_partition(US_PRICE_EOD_DATASET, "US", trading_day)
    assert len(rows) == first.rows_written


def _stub_result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="YFINANCE",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
        currentness_caveat="test fixture",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_job_us_fundamentals_writes_every_capability(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)
    _seed_universe(ctx, run_date, ["AAPL"])

    async def fake_fetch(
        self: object, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        key = {
            Capability.FINANCIALS: "lines",
            Capability.BALANCE_SHEET: "lines",
            Capability.CASH_FLOW_STATEMENT: "lines",
            Capability.DIVIDENDS: "events",
            Capability.SPLIT_PRICE: "rows",
        }[cap]
        return _stub_result({key: [{"symbol": sym.id, "value": 1}]})

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)

    run = await job_us_fundamentals(run_date, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 5  # one row per capability, one symbol
    assert ctx.store.has_partition("us_financials", "US", run_date)
    assert ctx.store.has_partition("us_dividends", "US", run_date)
    assert ctx.store.has_partition("us_split_price", "US", run_date)


async def test_job_us_fundamentals_one_symbol_failure_is_partial_not_fatal(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)
    _seed_universe(ctx, run_date, ["AAPL", "BAD"])

    async def fake_fetch(
        self: object, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        if sym.id == "BAD":
            raise RuntimeError("upstream boom")
        return _stub_result({"lines": [{"symbol": sym.id, "value": 1}]})

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)

    run = await job_us_fundamentals(run_date, ctx)

    assert run.status is RunStatus.PARTIAL
    assert run.rows_written > 0


async def test_job_us_fundamentals_without_yfinance_adapter_is_skipped(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, with_yfinance=False)
    run_date = date(2024, 1, 2)
    _seed_universe(ctx, run_date, ["AAPL"])

    run = await job_us_fundamentals(run_date, ctx)

    assert run.status is RunStatus.SKIPPED


async def test_backfill_us_price_range_fills_only_missing_trading_days(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    start = date(2024, 1, 1)
    end = date(2024, 1, 3)
    _seed_universe(ctx, start, ["AAPL"])

    async def fake_run_sync(fn: object, *args: object) -> pd.DataFrame:
        return _two_symbol_frame(date(2024, 1, 2))

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    runs = await backfill_us_price_range(ctx, start=start, end=end, only_missing=True)

    # 2024-01-01 is a holiday-agnostic Monday (real trading day under the plain weekday
    # fallback calendar); 01-01..01-03 spans 3 weekday business days.
    assert len(runs) == 3

    # Re-running only fills whatever's still missing — already-written dates are skipped.
    again = await backfill_us_price_range(ctx, start=start, end=end, only_missing=True)
    assert len(again) == 0
