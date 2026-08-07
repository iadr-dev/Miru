"""Ingest job tests: calendar-gating, idempotent re-run, leader stub."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import (
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    DIVIDENDS_JOB,
    FINANCIALS_JOB,
    MARKET_VALUE_JOB,
    PRICE_EOD_JOB,
    job_active_etf_holding_changes,
    job_balance_sheet,
    job_cash_flow,
    job_dividends,
    job_financials,
    job_market_value,
    job_price_eod,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _settings() -> Settings:
    return Settings(finmind_api_key="test-token")


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))


def _ctx(tmp_path: object, *, leader: object = None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=_settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),  # plain Mon-Fri fallback (Phase 3 default)
        tls_policy=TLSPolicy.STRICT,
    )


async def test_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Saturday must never attempt a fetch — calendar-gated before any network call."""

    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client on a non-trading day")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)

    run = await job_price_eod(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", saturday)


async def test_idempotent_rerun_same_date_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)  # a Tuesday

    first = await job_price_eod(trading_day, ctx)
    second = await job_price_eod(trading_day, ctx)

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written
    read_back = ctx.store.read_partition(PRICE_EOD_JOB.dataset, "TW", trading_day)
    assert len(read_back) == first.rows_written  # not doubled


async def test_leader_stub_always_leader_in_dev_writes(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev/single-process mode: `AlwaysLeader` returns True, so the write proceeds."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", trading_day)


async def test_leader_stub_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future real election implementation returns False when this process is a standby —
    `NeverLeader` exercises that branch: no fetch, no write, journaled as SKIPPED."""

    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())
    trading_day = date(2024, 1, 2)

    run = await job_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", trading_day)


async def test_financials_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same calendar gate as `job_price_eod` — a non-trading day never fetches, even though
    financials/dividends are irregular-cadence rather than daily-clock-time datasets."""

    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client on a non-trading day")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)

    run = await job_financials(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0
    assert not ctx.store.has_partition(FINANCIALS_JOB.dataset, "TW", saturday)


async def test_financials_idempotent_rerun_same_date_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "financials_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)  # a Tuesday

    first = await job_financials(trading_day, ctx)
    second = await job_financials(trading_day, ctx)

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written
    read_back = ctx.store.read_partition(FINANCIALS_JOB.dataset, "TW", trading_day)
    assert len(read_back) == first.rows_written  # not doubled


async def test_financials_leader_stub_always_leader_in_dev_writes(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "financials_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_financials(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(FINANCIALS_JOB.dataset, "TW", trading_day)


async def test_financials_leader_stub_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())
    trading_day = date(2024, 1, 2)

    run = await job_financials(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(FINANCIALS_JOB.dataset, "TW", trading_day)


async def test_dividends_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client on a non-trading day")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)

    run = await job_dividends(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0
    assert not ctx.store.has_partition(DIVIDENDS_JOB.dataset, "TW", saturday)


async def test_dividends_idempotent_rerun_same_date_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "dividends_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path)
    trading_day = date(2024, 1, 2)  # a Tuesday

    first = await job_dividends(trading_day, ctx)
    second = await job_dividends(trading_day, ctx)

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written
    read_back = ctx.store.read_partition(DIVIDENDS_JOB.dataset, "TW", trading_day)
    assert len(read_back) == first.rows_written  # not doubled


async def test_dividends_leader_stub_always_leader_in_dev_writes(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "dividends_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_dividends(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(DIVIDENDS_JOB.dataset, "TW", trading_day)


async def test_dividends_leader_stub_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())
    trading_day = date(2024, 1, 2)

    run = await job_dividends(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(DIVIDENDS_JOB.dataset, "TW", trading_day)


async def test_balance_sheet_leader_writes_and_reads_back(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "balance_sheet_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_balance_sheet(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(BALANCE_SHEET_JOB.dataset, "TW", trading_day)


async def test_balance_sheet_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())

    run = await job_balance_sheet(date(2024, 1, 2), ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(BALANCE_SHEET_JOB.dataset, "TW", date(2024, 1, 2))


async def test_cash_flow_leader_writes_and_reads_back(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "cash_flow_statement_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_cash_flow(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(CASH_FLOW_JOB.dataset, "TW", trading_day)


async def test_market_value_leader_writes_and_reads_back(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "market_value_2330.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2024, 1, 2)

    run = await job_market_value(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(MARKET_VALUE_JOB.dataset, "TW", trading_day)


async def test_market_value_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client on a non-trading day")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)

    run = await job_market_value(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(MARKET_VALUE_JOB.dataset, "TW", saturday)


async def test_active_etf_holding_changes_non_trading_day_is_skipped_with_no_fetch(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("must not construct an HTTP client on a non-trading day")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path)
    saturday = date(2024, 1, 6)

    run = await job_active_etf_holding_changes(saturday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0
    assert not ctx.store.has_partition(ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, "TW", saturday)


async def test_active_etf_holding_changes_idempotent_rerun_same_date_does_not_duplicate(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "active_etf_holding_change_whole_market.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path)
    trading_day = date(2025, 5, 7)  # a Wednesday

    first = await job_active_etf_holding_changes(trading_day, ctx)
    second = await job_active_etf_holding_changes(trading_day, ctx)

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert first.rows_written == second.rows_written
    read_back = ctx.store.read_partition(ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, "TW", trading_day)
    assert len(read_back) == first.rows_written  # not doubled


async def test_active_etf_holding_changes_leader_stub_always_leader_in_dev_writes(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "active_etf_holding_change_whole_market.json")
    _patch_client(monkeypatch, body)
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    trading_day = date(2025, 5, 7)

    run = await job_active_etf_holding_changes(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS
    assert ctx.store.has_partition(ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, "TW", trading_day)


async def test_active_etf_holding_changes_leader_stub_non_leader_blocks_write(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, leader=NeverLeader())
    trading_day = date(2025, 5, 7)

    run = await job_active_etf_holding_changes(trading_day, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, "TW", trading_day)
