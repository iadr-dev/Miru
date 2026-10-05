"""US whole-market ingest job tests: `job_us_price_eod` (batch_download row-mapping, calendar/
leader gating, empty-universe skip), `job_us_fundamentals` (per-symbol fan-out, partial-failure
journaling), and `backfill_us_price_range` (gap-fill over a date window)."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import anyio.to_thread
import pandas as pd
import pytest

import ingest.us_jobs as us_jobs_module
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
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass, TLSPolicy
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
            # `ANALYST_CONSENSUS`/`SECTOR_CLASS` return one row's own dict directly (see
            # `_fetch_one_fundamental`'s own docstring) — `INSTITUTIONAL_HOLDERS`/
            # `INSIDER_ACTIVITY` are list-shaped like the original five.
            Capability.ANALYST_CONSENSUS: "consensus",
            Capability.INSTITUTIONAL_HOLDERS: "holders",
            Capability.INSIDER_ACTIVITY: "activity",
            Capability.SECTOR_CLASS: "classification",
        }[cap]
        value: object = (
            {"symbol": sym.id, "value": 1}
            if cap in (Capability.ANALYST_CONSENSUS, Capability.SECTOR_CLASS)
            else [{"symbol": sym.id, "value": 1}]
        )
        return _stub_result({key: value})

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)

    run = await job_us_fundamentals(run_date, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 9  # one row per capability (9), one symbol
    assert ctx.store.has_partition("us_financials", "US", run_date)
    assert ctx.store.has_partition("us_dividends", "US", run_date)
    assert ctx.store.has_partition("us_split_price", "US", run_date)
    assert ctx.store.has_partition("us_analyst_consensus", "US", run_date)
    assert ctx.store.has_partition("us_institutional_holders", "US", run_date)
    assert ctx.store.has_partition("us_insider_activity", "US", run_date)
    assert ctx.store.has_partition("us_sector_class", "US", run_date)


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


async def test_job_us_fundamentals_rate_limited_result_counts_as_failure_not_success(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded (never-raised) `HealthStatus.UNREACHABLE` result — e.g. yfinance rate-limiting
    every call — must not be silently treated as a real, empty-but-successful fetch. Regression
    test for the real bug found on the first live run: 250 symbols x 5 capabilities with no
    inter-call delay got almost every call rate-limited, and the run still reported
    `RunStatus.SUCCESS` with `rows_written=0` because only raised exceptions were counted."""
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)
    _seed_universe(ctx, run_date, ["AAPL", "MSFT"])

    async def fake_fetch(
        self: object, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        return SourceResult(
            source_id="YFINANCE",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
            currentness_caveat="test fixture",
            payload={},
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="Too Many Requests. Rate limited."
            ),
        )

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)

    run = await job_us_fundamentals(run_date, ctx)

    assert run.status is RunStatus.FAILED
    assert run.rows_written == 0


async def test_job_us_fundamentals_throttles_between_calls(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real inter-call delay must actually run between fetches — not just exist as an unused
    constant. Confirms `asyncio.sleep` is invoked (symbols x capabilities - 1) times, never on
    the very first call."""
    ctx = _ctx(tmp_path)
    run_date = date(2024, 1, 2)
    _seed_universe(ctx, run_date, ["AAPL", "MSFT"])

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    async def fake_fetch(
        self: object, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        return _stub_result({"lines": [{"symbol": sym.id, "value": 1}]})

    monkeypatch.setattr(yfinance_adapter_module.YFinanceAdapter, "fetch", fake_fetch)
    monkeypatch.setattr(us_jobs_module.asyncio, "sleep", fake_sleep)

    await job_us_fundamentals(run_date, ctx)

    # 2 symbols x 9 capabilities = 18 calls, 17 delays (never before the first call).
    assert len(sleep_calls) == 17


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


async def test_backfill_us_price_range_throttles_between_dates(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real inter-date pause must run between `batch_download` calls within one backfill —
    regression test for the real bug found on the first live US backfill: with no pause,
    per-day symbol coverage degraded across consecutive calls (244/250 -> 44/250 within ~3
    months of simulated trading days) until the run stopped making progress entirely."""
    ctx = _ctx(tmp_path)
    start = date(2024, 1, 1)
    end = date(2024, 1, 3)
    _seed_universe(ctx, start, ["AAPL"])
    _patch_batch_download(monkeypatch, _two_symbol_frame(date(2024, 1, 2)))

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(us_jobs_module.asyncio, "sleep", fake_sleep)

    runs = await backfill_us_price_range(ctx, start=start, end=end, only_missing=True)

    # 3 dates, 2 pauses (never before the first call).
    assert len(runs) == 3
    assert len(sleep_calls) == 2


# ---- user-demanded symbols: alerts outside the screener universe still get price data -----------

from collections.abc import AsyncGenerator  # noqa: E402
from dataclasses import replace  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.base import Base  # noqa: E402
from db.models import AlertRuleRow, WatchlistItemRow, WatchlistRow  # noqa: E402
from db.sqlite_pragma import enable_sqlite_foreign_keys  # noqa: E402
from ingest.us_jobs import MAX_DEMANDED_US_SYMBOLS, demanded_us_symbols  # noqa: E402
from tests.scheduler.conftest import insert_user  # noqa: E402

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add_rule(  # noqa: PLR0913 - one keyword per rule column under test
    factory: async_sessionmaker[AsyncSession],
    user: str,
    *,
    rule_id: str,
    symbol: str | None = None,
    market: str | None = "US",
    watchlist_id: str | None = None,
    enabled: bool = True,
) -> None:
    async with factory() as session:
        session.add(
            AlertRuleRow(
                id=rule_id,
                user_id=user,
                symbol=symbol,
                market=market if symbol is not None else None,
                watchlist_id=watchlist_id,
                condition={"kind": "attention_entry"},
                enabled=enabled,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()


async def _add_watchlist(
    factory: async_sessionmaker[AsyncSession], user: str, members: list[tuple[str, str]]
) -> None:
    async with factory() as session:
        session.add(
            WatchlistRow(
                id="wl",
                user_id=user,
                name="w",
                market="US",
                asset_class="stock",
                sort_order="a",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.flush()
        for i, (symbol, market) in enumerate(members):
            session.add(
                WatchlistItemRow(
                    id=f"i{i}",
                    watchlist_id="wl",
                    user_id=user,
                    symbol=symbol,
                    market=market,
                    asset_class="stock",
                    sort_order=str(i),
                    added_at=_NOW,
                )
            )
        await session.commit()


async def test_demanded_symbols_is_empty_without_a_database(tmp_path: object) -> None:
    assert await demanded_us_symbols(_ctx(tmp_path)) == []


async def test_demanded_symbols_collects_enabled_us_alerts_only(
    tmp_path: object, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _add_rule(session_factory, user, rule_id="a", symbol="NVDA")
    await _add_rule(session_factory, user, rule_id="b", symbol="tsla")  # stored lower-case
    await _add_rule(session_factory, user, rule_id="c", symbol="AMD", enabled=False)
    await _add_rule(session_factory, user, rule_id="d", symbol="2330", market="TW")
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    assert await demanded_us_symbols(ctx) == ["NVDA", "TSLA"]


async def test_demanded_symbols_includes_us_members_of_watchlist_scoped_rules(
    tmp_path: object, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _add_watchlist(session_factory, user, [("AAPL", "US"), ("MSFT", "US"), ("2330", "TW")])
    await _add_rule(session_factory, user, rule_id="w", market=None, watchlist_id="wl")
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    assert await demanded_us_symbols(ctx) == ["AAPL", "MSFT"]  # the TW member is not US demand


async def test_a_watchlist_without_an_enabled_rule_adds_no_demand(
    tmp_path: object, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _add_watchlist(session_factory, user, [("AAPL", "US")])
    await _add_rule(
        session_factory, user, rule_id="w", market=None, watchlist_id="wl", enabled=False
    )
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    assert await demanded_us_symbols(ctx) == []


async def test_demanded_symbols_are_capped_deterministically(
    tmp_path: object,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    for i, symbol in enumerate(["CCC", "AAA", "BBB", "DDD"]):
        await _add_rule(session_factory, user, rule_id=str(i), symbol=symbol)
    monkeypatch.setattr(us_jobs_module, "MAX_DEMANDED_US_SYMBOLS", 2)
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    assert await demanded_us_symbols(ctx) == ["AAA", "BBB"]
    assert MAX_DEMANDED_US_SYMBOLS >= 100  # the real cap is a sane size, not a token one


async def test_price_job_fetches_alerted_symbols_even_with_no_universe(
    tmp_path: object,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _add_rule(session_factory, user, rule_id="a", symbol="AAPL")
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    trading_day = date(2024, 1, 2)
    requested: list[list[str]] = []

    async def fake_download(symbols: list[str], **_kwargs: object) -> pd.DataFrame:
        requested.append(list(symbols))
        return _two_symbol_frame(trading_day)

    monkeypatch.setattr(us_jobs_module, "batch_download", fake_download)

    run = await job_us_price_eod(trading_day, ctx)

    assert run.status is RunStatus.SUCCESS  # previously SKIPPED: "no US symbol universe yet"
    assert requested == [["AAPL", "SPY"]]  # the alerted symbol + the always-on reference index


async def test_price_job_merges_universe_and_demand_without_duplicating_spy(
    tmp_path: object,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _add_rule(session_factory, user, rule_id="a", symbol="AAPL")  # also in the universe
    await _add_rule(session_factory, user, rule_id="b", symbol="SPY")  # demanded reference index
    ctx = replace(_ctx(tmp_path), session_factory=session_factory)
    trading_day = date(2024, 1, 2)
    _seed_universe(ctx, trading_day, ["AAPL", "MSFT"])
    requested: list[list[str]] = []

    async def fake_download(symbols: list[str], **_kwargs: object) -> pd.DataFrame:
        requested.append(list(symbols))
        return _two_symbol_frame(trading_day)

    monkeypatch.setattr(us_jobs_module, "batch_download", fake_download)

    await job_us_price_eod(trading_day, ctx)

    assert requested == [["AAPL", "MSFT", "SPY"]]  # merged, de-duplicated, SPY exactly once
