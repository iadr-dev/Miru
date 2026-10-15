"""`job_us_fundamentals` refetches only what is stale, keeps what it skipped, and fetches listed
symbols before OTC ones.

Before: every run pulled all nine capabilities for every symbol (about twelve hours for the 2,123
symbols in the US universe) to refresh data that mostly had not changed."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

import ingest.us_jobs as us_jobs_module
import sources.yfinance._adapter as yfinance_adapter_module
from ingest.journal import RunStatus
from ingest.us_fundamentals_plan import STATE_DATASET
from ingest.us_jobs import job_us_fundamentals
from ingest.us_universe import US_SYMBOL_UNIVERSE_DATASET
from schemas.enums import Capability, Market
from schemas.market import Symbol
from schemas.source import SourceResult
from tests.ingest.test_us_jobs import _ctx, _stub_result

DAY0 = date(2026, 10, 5)
KEYS = {
    Capability.FINANCIALS: "lines",
    Capability.BALANCE_SHEET: "lines",
    Capability.CASH_FLOW_STATEMENT: "lines",
    Capability.DIVIDENDS: "events",
    Capability.SPLIT_PRICE: "rows",
    Capability.ANALYST_CONSENSUS: "consensus",
    Capability.INSTITUTIONAL_HOLDERS: "holders",
    Capability.INSIDER_ACTIVITY: "activity",
    Capability.SECTOR_CLASS: "classification",
}
SINGLE_ROW = (Capability.ANALYST_CONSENSUS, Capability.SECTOR_CLASS)
STATEMENTS = {Capability.FINANCIALS, Capability.BALANCE_SHEET, Capability.CASH_FLOW_STATEMENT}
WEEKLY = {
    Capability.DIVIDENDS,
    Capability.SPLIT_PRICE,
    Capability.ANALYST_CONSENSUS,
    Capability.INSTITUTIONAL_HOLDERS,
    Capability.INSIDER_ACTIVITY,
}


class _Yahoo:
    """A fake Yahoo: records every call and answers with a version number that grows per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Capability]] = []
        self.version = 1
        self.fail: set[tuple[str, Capability]] = set()
        self.empty: set[Capability] = set()

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        self.calls.append((sym.id, cap))
        if (sym.id, cap) in self.fail:
            raise RuntimeError("upstream boom")
        key = KEYS[cap]
        if cap in self.empty:
            return _stub_result({key: []})
        row = {"symbol": sym.id, "value": self.version}
        return _stub_result({key: row if cap in SINGLE_ROW else [row]})

    def caps_for(self, symbol: str) -> set[Capability]:
        return {cap for sym, cap in self.calls if sym == symbol}


def _universe(ctx, symbols: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    rows = [{"symbol": s, "name": s, "exchange": ex} for s, ex in symbols.items()]
    ctx.store.write_partition(US_SYMBOL_UNIVERSE_DATASET, Market.US.value, DAY0, rows)


@pytest.fixture
def yahoo(monkeypatch: pytest.MonkeyPatch) -> _Yahoo:
    fake = _Yahoo()

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        yfinance_adapter_module.YFinanceAdapter, "fetch", lambda self, *a, **k: fake.fetch(*a, **k)
    )
    monkeypatch.setattr(us_jobs_module.asyncio, "sleep", no_sleep)
    return fake


def _values(ctx, dataset: str, run_date: date) -> dict[str, int]:  # type: ignore[no-untyped-def]
    rows = ctx.store.read_partition(dataset, Market.US.value, run_date)
    return {str(r["symbol"]): int(r["value"]) for r in rows}


async def test_the_first_run_fetches_everything(tmp_path: Path, yahoo: _Yahoo) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS", "TCEHY": "OTC Markets OTCPK"})

    run = await job_us_fundamentals(DAY0, ctx)

    assert run.status is RunStatus.SUCCESS
    assert len(yahoo.calls) == 2 * 9


async def test_a_week_later_only_the_weekly_data_of_listed_symbols_is_fetched(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS", "TCEHY": "OTC Markets OTCPK"})
    await job_us_fundamentals(DAY0, ctx)
    yahoo.calls.clear()

    await job_us_fundamentals(DAY0 + timedelta(days=7), ctx)

    # Listed: consensus/holders/insiders/dividends/splits are a week old; statements (25 days) and
    # sector (90) are not. OTC: nothing is due before 30 days.
    assert yahoo.caps_for("AAPL") == WEEKLY
    assert yahoo.caps_for("TCEHY") == set()


async def test_otc_symbols_are_refreshed_monthly_and_their_statements_after_45_days(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"TCEHY": "OTC Markets OTCPK"})
    await job_us_fundamentals(DAY0, ctx)

    yahoo.calls.clear()
    await job_us_fundamentals(DAY0 + timedelta(days=35), ctx)
    assert yahoo.caps_for("TCEHY") == WEEKLY

    yahoo.calls.clear()
    await job_us_fundamentals(DAY0 + timedelta(days=49), ctx)
    # the weekly data was refreshed at day 35, so only the statements (45 days) are due now
    assert yahoo.caps_for("TCEHY") == STATEMENTS


async def test_a_symbol_that_was_not_refetched_keeps_its_rows(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS", "TCEHY": "OTC Markets OTCPK"})
    await job_us_fundamentals(DAY0, ctx)
    yahoo.version = 2

    later = DAY0 + timedelta(days=7)
    await job_us_fundamentals(later, ctx)

    # The statements were not refetched for anyone: still version 1, still both symbols.
    assert _values(ctx, "us_financials", later) == {"AAPL": 1, "TCEHY": 1}
    # The weekly data was refetched for the listed symbol only: it has the new version, and the
    # OTC symbol's older rows are still there.
    assert _values(ctx, "us_insider_activity", later) == {"AAPL": 2, "TCEHY": 1}


async def test_a_refetch_replaces_the_old_rows_not_adds_to_them(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS"})
    await job_us_fundamentals(DAY0, ctx)
    yahoo.version = 2

    later = DAY0 + timedelta(days=28)
    await job_us_fundamentals(later, ctx)

    rows = ctx.store.read_partition("us_financials", Market.US.value, later)
    assert [(r["symbol"], r["value"]) for r in rows] == [("AAPL", 2)]


async def test_an_empty_answer_does_not_wipe_what_was_stored(tmp_path: Path, yahoo: _Yahoo) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS"})
    await job_us_fundamentals(DAY0, ctx)
    yahoo.empty = {Capability.FINANCIALS}  # Yahoo answers with nothing this time

    later = DAY0 + timedelta(days=28)
    await job_us_fundamentals(later, ctx)

    assert _values(ctx, "us_financials", later) == {"AAPL": 1}


async def test_a_failed_fetch_is_retried_at_the_next_run_not_after_the_window(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS"})
    yahoo.fail = {("AAPL", Capability.INSIDER_ACTIVITY)}
    first = await job_us_fundamentals(DAY0, ctx)
    assert first.status is RunStatus.PARTIAL

    yahoo.fail = set()
    yahoo.calls.clear()
    await job_us_fundamentals(DAY0 + timedelta(days=1), ctx)

    assert yahoo.caps_for("AAPL") == {Capability.INSIDER_ACTIVITY}


async def test_listed_symbols_are_fetched_before_otc_ones(tmp_path: Path, yahoo: _Yahoo) -> None:
    ctx = _ctx(tmp_path)
    _universe(
        ctx,
        {
            "OTC1": "OTC Markets OTCPK",
            "NYSE1": "NYSE",
            "OTC2": "OTC Markets OTCQX",
            "NASD1": "NasdaqGS",
        },
    )

    await job_us_fundamentals(DAY0, ctx)

    order = list(dict.fromkeys(sym for sym, _ in yahoo.calls))
    assert order[:2] == ["NYSE1", "NASD1"]
    assert set(order[2:]) == {"OTC1", "OTC2"}


async def test_a_run_with_no_state_starts_from_what_the_partitions_already_hold(
    tmp_path: Path, yahoo: _Yahoo
) -> None:
    """The run in progress when this shipped wrote its partitions but no state: next week must
    not refetch everything because of that."""
    ctx = _ctx(tmp_path)
    _universe(ctx, {"AAPL": "NasdaqGS"})
    for dataset in (
        "us_financials",
        "us_balance_sheet",
        "us_cash_flow",
        "us_dividends",
        "us_split_price",
        "us_analyst_consensus",
        "us_institutional_holders",
        "us_insider_activity",
        "us_sector_class",
    ):
        ctx.store.write_partition(dataset, Market.US.value, DAY0, [{"symbol": "AAPL", "value": 1}])
    assert not ctx.store.has_partition(STATE_DATASET, Market.US.value, DAY0)

    await job_us_fundamentals(DAY0 + timedelta(days=7), ctx)

    assert yahoo.caps_for("AAPL") == WEEKLY
