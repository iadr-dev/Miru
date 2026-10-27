"""Market value and its weights derived from stored closes and the shares-outstanding snapshot.

The derivation (`ingest/jobs/market_weight.py`) is driven with a hand-made snapshot and price
partition; its place in the ordered chain (derived first, FinMind after) through the shared runner.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest

from ingest.context import IngestContext
from ingest.jobs import (
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    PRICE_EOD_JOB,
    run_whole_market_job,
)
from ingest.jobs.market_weight import DerivedMarketValueSource
from ingest.jobs.source_chain import DERIVED_ID, FINMIND_ID, resolve_chain
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.capabilities import MarketValueRow, MarketValueWeightRow
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus
from sources.company_shares import LISTED, OTC, ShareEntry, SharesSnapshot
from sources.exchange_whole_market._common import ExchangeFetchError
from tests.ingest.test_whole_market_runner import (
    TRADING_DAY,
    RecordingPublisher,
    StubSource,
    _bar,
    _result,
)

SNAPSHOT_DATE = date(2024, 1, 3)  # the day after TRADING_DAY: the dump is read in the evening


def _entry(symbol: str, board: str, shares: int, name: str = "") -> ShareEntry:
    return ShareEntry(symbol, name or f"name{symbol}", board, shares)


def _snapshot(
    *entries: ShareEntry, as_of: date = SNAPSHOT_DATE, without_shares: int = 0
) -> SharesSnapshot:
    return SharesSnapshot(
        as_of=as_of, entries=entries, without_shares=without_shares, depositary_receipts=0
    )


# Five listed + three OTC companies with round numbers: market values 1000, 500, 300, 100, 100 and
# 200, 150, 50 (NT$ per share 1.0 for the sake of arithmetic).
ENTRIES = (
    _entry("2330", LISTED, 1000, "TSMC"),
    _entry("2317", LISTED, 500, "HonHai"),
    _entry("2412", LISTED, 300),
    _entry("1101", LISTED, 100),
    _entry("1102", LISTED, 100),
    _entry("6223", OTC, 200),
    _entry("5274", OTC, 150),
    _entry("3105", OTC, 50),
)
TOTAL = 1000 + 500 + 300 + 100 + 100 + 200 + 150 + 50


class FakeShares:
    def __init__(self, snapshot: SharesSnapshot | ExchangeFetchError) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def fetch_snapshot(self, *, tls_policy: TLSPolicy) -> SharesSnapshot:
        self.calls += 1
        if isinstance(self.snapshot, ExchangeFetchError):
            raise self.snapshot
        return self.snapshot


def _store(tmp_path: Path, symbols: tuple[str, ...] | None = None, **closes: float) -> ParquetStore:
    """A store holding TRADING_DAY's price partition: every snapshot company at close 1.0, except
    those in `symbols` (when given: only these) and any `close=` overrides by `s<symbol>`."""
    store = ParquetStore(root=tmp_path)
    wanted = symbols if symbols is not None else tuple(e.symbol for e in ENTRIES)
    rows = [_bar(s, close=closes.get(f"s{s}", 1.0)) for s in wanted]
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", TRADING_DAY, rows)
    return store


def _source(
    store: ParquetStore, shares: FakeShares | None = None, **kwargs: int
) -> DerivedMarketValueSource:
    return DerivedMarketValueSource(store, shares or FakeShares(_snapshot(*ENTRIES)), **kwargs)


async def _ask(source: DerivedMarketValueSource, cap: Capability, day: date = TRADING_DAY):
    return await source.fetch_whole_market(
        cap, tls_policy=TLSPolicy.STRICT, start_date=day.isoformat()
    )


def _rows(result: object) -> list[dict[str, object]]:
    payload = result.payload  # type: ignore[attr-defined]
    rows = payload["rows"]
    assert isinstance(rows, list)
    return rows


async def test_weight_is_the_market_value_share_over_both_boards(tmp_path: Path) -> None:
    result = await _ask(_source(_store(tmp_path)), Capability.MARKET_VALUE_WEIGHT)

    assert result.health.status is HealthStatus.OK
    rows = _rows(result)
    for row in rows:
        MarketValueWeightRow.model_validate(row)  # the stored row model, field for field
    by_symbol = {str(r["symbol"]): r for r in rows}
    assert by_symbol["2330"]["weight_per"] == pytest.approx(100.0 * 1000 / TOTAL)
    assert by_symbol["2330"]["rank"] == 1
    assert by_symbol["2330"]["stock_name"] == "TSMC"
    assert by_symbol["2330"]["listing_type"] == "twse"
    assert by_symbol["6223"]["listing_type"] == "tpex"
    assert sum(float(str(r["weight_per"])) for r in rows) == pytest.approx(100.0, abs=1e-4)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert {r["trade_date"] for r in rows} == {"2024-01-02"}
    assert {r["market"] for r in rows} == {"TW"}


async def test_ties_are_ranked_by_symbol(tmp_path: Path) -> None:
    rows = _rows(await _ask(_source(_store(tmp_path)), Capability.MARKET_VALUE_WEIGHT))

    tied = [str(r["symbol"]) for r in rows if r["symbol"] in {"1101", "1102"}]
    assert tied == ["1101", "1102"]


async def test_market_value_is_close_times_shares(tmp_path: Path) -> None:
    store = _store(tmp_path, s2330=104.5)

    rows = _rows(await _ask(_source(store), Capability.MARKET_VALUE))

    for row in rows:
        MarketValueRow.model_validate(row)
    by_symbol = {str(r["symbol"]): r["market_value"] for r in rows}
    assert by_symbol["2330"] == round(104.5 * 1000)
    assert by_symbol["6223"] == 200
    assert isinstance(by_symbol["2330"], int)


async def test_a_company_without_a_close_is_skipped_and_counted(tmp_path: Path) -> None:
    store = _store(tmp_path, symbols=tuple(e.symbol for e in ENTRIES if e.symbol != "1102"))

    result = await _ask(_source(store), Capability.MARKET_VALUE_WEIGHT)

    rows = _rows(result)
    assert "1102" not in {r["symbol"] for r in rows}
    assert sum(float(str(r["weight_per"])) for r in rows) == pytest.approx(100.0, abs=1e-4)
    assert "1 without a close skipped" in result.health.detail
    assert "close x shares as of 2024-01-03" in result.health.detail


async def test_a_zero_close_is_no_close(tmp_path: Path) -> None:
    store = _store(tmp_path, s1102=0.0)

    result = await _ask(_source(store), Capability.MARKET_VALUE)

    assert "1102" not in {r["symbol"] for r in _rows(result)}
    assert "1 without a close skipped" in result.health.detail


async def test_companies_without_a_share_count_are_counted_from_the_snapshot(
    tmp_path: Path,
) -> None:
    shares = FakeShares(_snapshot(*ENTRIES, without_shares=3))

    result = await _ask(_source(_store(tmp_path), shares), Capability.MARKET_VALUE)

    assert "3 without a share count skipped" in result.health.detail


async def test_a_priced_symbol_with_no_share_count_is_not_invented(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        PRICE_EOD_JOB.dataset,
        "TW",
        TRADING_DAY,
        [_bar(e.symbol) for e in ENTRIES] + [_bar("0050", close=190.0)],  # an ETF
    )

    rows = _rows(await _ask(_source(store), Capability.MARKET_VALUE_WEIGHT))

    assert "0050" not in {r["symbol"] for r in rows}


async def test_no_price_partition_yet_is_not_published_yet(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    shares = FakeShares(_snapshot(*ENTRIES))

    result = await _ask(_source(store, shares), Capability.MARKET_VALUE_WEIGHT)

    assert result.health.status is HealthStatus.STALE
    assert _rows(result) == []
    assert "no price partition" in result.health.detail
    assert shares.calls == 0  # nothing to derive from: the dump is not even fetched


async def test_one_board_missing_from_the_prices_is_never_half_a_market(tmp_path: Path) -> None:
    listed_only = tuple(e.symbol for e in ENTRIES if e.listing_type == LISTED)

    result = await _ask(_source(_store(tmp_path, symbols=listed_only)), Capability.MARKET_VALUE)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []
    assert "only 0 of 3 TPEx companies" in result.health.detail


async def test_a_day_long_before_the_snapshot_is_not_derived(tmp_path: Path) -> None:
    far_snapshot = FakeShares(_snapshot(*ENTRIES, as_of=date(2024, 1, 20)))

    result = await _ask(_source(_store(tmp_path), far_snapshot), Capability.MARKET_VALUE_WEIGHT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert _rows(result) == []
    assert "no history of share counts" in result.health.detail


async def test_the_lag_limit_is_inclusive_and_configurable(tmp_path: Path) -> None:
    snapshot = FakeShares(_snapshot(*ENTRIES, as_of=date(2024, 1, 9)))  # 7 days after

    at_limit = await _ask(_source(_store(tmp_path), snapshot), Capability.MARKET_VALUE)
    tight = await _ask(_source(_store(tmp_path), snapshot, max_lag_days=6), Capability.MARKET_VALUE)

    assert at_limit.health.status is HealthStatus.OK
    assert tight.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_a_stale_snapshot_is_refused(tmp_path: Path) -> None:
    old = FakeShares(_snapshot(*ENTRIES, as_of=date(2023, 12, 1)))

    result = await _ask(_source(_store(tmp_path), old), Capability.MARKET_VALUE)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "looks stale" in result.health.detail


async def test_an_unreadable_dump_is_unreachable_with_the_reason(tmp_path: Path) -> None:
    failing = FakeShares(ExchangeFetchError("www.tpex.org.tw: HTTP 403"))

    result = await _ask(_source(_store(tmp_path), failing), Capability.MARKET_VALUE)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "shares outstanding: www.tpex.org.tw: HTTP 403"


async def test_other_capabilities_and_bad_dates_are_refused(tmp_path: Path) -> None:
    source = _source(_store(tmp_path))

    other = await _ask(source, Capability.PRICE_EOD)
    bad = await source.fetch_whole_market(
        Capability.MARKET_VALUE, tls_policy=TLSPolicy.STRICT, start_date="soon"
    )

    assert other.health.status is HealthStatus.UNAVAILABLE_TIER
    assert bad.health.status is HealthStatus.UNREACHABLE


# ---- the ordered chain through the shared runner -------------------------------------------


def _ctx(
    tmp_path: Path,
    finmind: StubSource,
    derived: DerivedMarketValueSource | None,
    publisher: RecordingPublisher | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        derived=derived,
        store=ParquetStore(root=tmp_path),
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher or RecordingPublisher(),
    )


def _finmind_weight() -> StubSource:
    return StubSource(_result([], status=HealthStatus.UNAVAILABLE_TIER, detail="plan"))


@pytest.mark.parametrize("cap", [Capability.MARKET_VALUE, Capability.MARKET_VALUE_WEIGHT])
def test_the_chain_asks_the_derivation_before_finmind(tmp_path: Path, cap: Capability) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([])), _source(_store(tmp_path / "s")))

    assert [link.source_id for link in resolve_chain(ctx, cap)] == [DERIVED_ID, FINMIND_ID]


def test_without_the_derivation_wired_finmind_is_alone(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([])), None)

    chain = resolve_chain(ctx, Capability.MARKET_VALUE_WEIGHT)

    assert [link.source_id for link in chain] == [FINMIND_ID]


async def test_the_runner_writes_the_derived_weights_and_names_the_snapshot(
    tmp_path: Path,
) -> None:
    derived = _source(_store(tmp_path))  # the same store the context writes to
    finmind = _finmind_weight()
    ctx = _ctx(tmp_path, finmind, derived)

    run = await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == len(ENTRIES)
    assert run.detail.startswith(
        "served by market_value_derived: close x shares as of 2024-01-03: TWSE 5 + TPEx 3 companies"
    )
    assert finmind.calls == []  # a healthy derived answer never reaches FinMind
    stored = ctx.store.read_partition(MARKET_VALUE_WEIGHT_JOB.dataset, "TW", TRADING_DAY)
    assert {r["symbol"] for r in stored} == {e.symbol for e in ENTRIES}
    market_value = await run_whole_market_job(MARKET_VALUE_JOB, TRADING_DAY, ctx)
    assert market_value.rows_written == len(ENTRIES)


async def test_no_price_partition_yet_falls_through_and_is_not_written(tmp_path: Path) -> None:
    derived = _source(ParquetStore(root=tmp_path))
    finmind = _finmind_weight()
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, finmind, derived, publisher)

    run = await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.FAILED  # retried by the next run once the prices land
    assert "market_value_derived: no rows" in run.detail
    assert not ctx.store.has_partition(MARKET_VALUE_WEIGHT_JOB.dataset, "TW", TRADING_DAY)
    assert publisher.signals == []


async def test_an_old_day_with_finmind_denied_is_a_skip_not_a_failure(tmp_path: Path) -> None:
    far = FakeShares(_snapshot(*ENTRIES, as_of=date(2024, 3, 1)))
    derived = _source(_store(tmp_path), far)
    ctx = _ctx(tmp_path, _finmind_weight(), derived)

    run = await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.tier_skipped
    assert not ctx.store.has_partition(MARKET_VALUE_WEIGHT_JOB.dataset, "TW", TRADING_DAY)


async def test_finmind_serves_when_the_derivation_cannot_and_the_plan_allows(
    tmp_path: Path,
) -> None:
    failing = FakeShares(ExchangeFetchError("openapi.twse.com.tw: HTTP 503"))
    derived = _source(_store(tmp_path), failing)
    weight_row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2024-01-02",
        "rank": 1,
        "stock_name": "TSMC",
        "weight_per": 30.0,
        "listing_type": "twse",
    }
    finmind = StubSource(_result([weight_row], detail="ok"))
    ctx = _ctx(tmp_path, finmind, derived)

    run = await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, TRADING_DAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail.startswith("served by finmind: ok (after market_value_derived: shares")
    assert len(finmind.calls) == 1
