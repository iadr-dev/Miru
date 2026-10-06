"""`job_etf_aum` — derives AUM from the ingested `etf_nav` partition (TWSE units outstanding x
prior-day NAV); Yahoo is only the per-symbol fallback for an ETF whose NAV row lacks an input."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import ETF_AUM_DATASET, ETF_NAV_DATASET, job_etf_aum
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.finmind import FinMindAdapter

_RUN_DATE = date(2026, 10, 5)


def _nav_row(symbol: str, units: int | None, nav: float | None) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "as_of": "2026-10-05",
        "fund_name": symbol,
        "units_outstanding": units,
        "prior_day_nav": nav,
    }


class _StubYahoo:
    """Stands in for `YFinanceAdapter`; records which symbols fell through to it."""

    def __init__(self, aum_by_symbol: dict[str, float]) -> None:
        self._aum_by_symbol = aum_by_symbol
        self.calls: list[str] = []

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **_: object
    ) -> SourceResult:
        assert cap is Capability.ETF_AUM
        self.calls.append(sym.id)
        aum = self._aum_by_symbol.get(sym.id)
        if aum is None:
            health = SourceHealth(status=HealthStatus.UNREACHABLE, detail="no totalAssets")
            payload: dict[str, object] = {}
        else:
            health = SourceHealth(status=HealthStatus.OK, detail="ok")
            payload = {
                "aum": {
                    "symbol": sym.id,
                    "market": "TW",
                    "as_of": _RUN_DATE.isoformat(),
                    "aum": aum,
                }
            }
        return SourceResult(
            source_id="YFINANCE",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="test caveat",
            payload=payload,
            health=health,
        )


def _ctx(
    tmp_path: object, *, yahoo: _StubYahoo | None = None, leader: object = None
) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        yfinance=yahoo,  # type: ignore[arg-type]
    )


def _seed_nav(ctx: IngestContext, rows: list[dict[str, object]]) -> None:
    ctx.store.write_partition(ETF_NAV_DATASET, Market.TW.value, _RUN_DATE, rows)


def _aum_by_symbol(ctx: IngestContext) -> dict[str, float]:
    rows = ctx.store.read_partition(ETF_AUM_DATASET, Market.TW.value, _RUN_DATE)
    return {str(row["symbol"]): float(row["aum"]) for row in rows}  # type: ignore[arg-type]


async def test_aum_is_units_times_prior_day_nav_without_calling_yahoo(tmp_path: object) -> None:
    yahoo = _StubYahoo({})
    ctx = _ctx(tmp_path, yahoo=yahoo)
    _seed_nav(ctx, [_nav_row("0050", 1_000, 100.0), _nav_row("00679B", 2_000, 25.5)])

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 2
    assert _aum_by_symbol(ctx) == {"0050": 100_000.0, "00679B": 51_000.0}
    assert yahoo.calls == []
    assert "2 derived" in run.detail


async def test_works_without_a_yahoo_adapter_when_every_etf_is_derivable(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, yahoo=None)
    _seed_nav(ctx, [_nav_row("0050", 10, 2.0)])

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert _aum_by_symbol(ctx) == {"0050": 20.0}


async def test_yahoo_is_only_called_for_etfs_missing_an_input(tmp_path: object) -> None:
    yahoo = _StubYahoo({"00980A": 777.0})
    ctx = _ctx(tmp_path, yahoo=yahoo)
    _seed_nav(
        ctx,
        [_nav_row("0050", 1_000, 100.0), _nav_row("00980A", None, 10.0)],  # no units yet
    )

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert yahoo.calls == ["00980A"]
    assert _aum_by_symbol(ctx) == {"0050": 100_000.0, "00980A": 777.0}
    assert "1 derived" in run.detail and "1 from Yahoo" in run.detail


@pytest.mark.parametrize(("units", "nav"), [(0, 10.0), (1_000, 0.0), (-5, 10.0), (None, None)])
async def test_non_positive_or_missing_inputs_are_never_written_as_a_guess(
    tmp_path: object, units: int | None, nav: float | None
) -> None:
    ctx = _ctx(tmp_path, yahoo=_StubYahoo({}))
    _seed_nav(ctx, [_nav_row("0050", 1_000, 100.0), _nav_row("00999", units, nav)])

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert "00999" not in _aum_by_symbol(ctx)
    assert run.status is RunStatus.PARTIAL  # the fallback found nothing either
    assert run.rows_written == 1


async def test_fallback_without_a_yahoo_adapter_counts_as_failure_not_crash(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, yahoo=None)
    _seed_nav(ctx, [_nav_row("0050", 1_000, 100.0), _nav_row("00999", None, None)])

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.PARTIAL
    assert _aum_by_symbol(ctx) == {"0050": 100_000.0}


async def test_no_nav_roster_is_a_graceful_skip(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, yahoo=_StubYahoo({}))

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(ETF_AUM_DATASET, Market.TW.value, _RUN_DATE)


async def test_non_leader_writes_nothing(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, yahoo=_StubYahoo({}), leader=NeverLeader())
    _seed_nav(ctx, [_nav_row("0050", 1_000, 100.0)])

    run = await job_etf_aum(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(ETF_AUM_DATASET, Market.TW.value, _RUN_DATE)


def _seed_nav_on(ctx: IngestContext, day: date, rows: list[dict[str, object]]) -> None:
    ctx.store.write_partition(ETF_NAV_DATASET, Market.TW.value, day, rows)


async def test_a_past_run_date_derives_from_that_days_nav_not_a_later_one(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, yahoo=None)
    _seed_nav_on(ctx, date(2026, 10, 1), [_nav_row("0050", 1_000, 100.0)])
    _seed_nav_on(ctx, date(2026, 10, 5), [_nav_row("0050", 2_000, 200.0)])

    past = await job_etf_aum(date(2026, 10, 2), ctx)  # weekday after 10-01, before 10-05
    live = await job_etf_aum(date(2026, 10, 5), ctx)

    assert past.status is RunStatus.SUCCESS and live.status is RunStatus.SUCCESS
    past_rows = ctx.store.read_partition(ETF_AUM_DATASET, Market.TW.value, date(2026, 10, 2))
    live_rows = ctx.store.read_partition(ETF_AUM_DATASET, Market.TW.value, date(2026, 10, 5))
    assert [row["aum"] for row in past_rows] == [100_000.0]  # 10-01's units x NAV
    assert [row["aum"] for row in live_rows] == [400_000.0]  # 10-05's own


async def test_a_run_date_after_every_nav_partition_uses_the_latest(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, yahoo=None)
    _seed_nav_on(ctx, date(2026, 10, 1), [_nav_row("0050", 1_000, 100.0)])

    run = await job_etf_aum(date(2026, 10, 9), ctx)

    assert run.status is RunStatus.SUCCESS
    rows = ctx.store.read_partition(ETF_AUM_DATASET, Market.TW.value, date(2026, 10, 9))
    assert [row["aum"] for row in rows] == [100_000.0]


async def test_a_run_date_before_any_nav_partition_is_skipped_not_derived_from_the_future(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, yahoo=None)
    _seed_nav_on(ctx, date(2026, 10, 5), [_nav_row("0050", 1_000, 100.0)])

    run = await job_etf_aum(date(2026, 9, 1), ctx)

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(ETF_AUM_DATASET, Market.TW.value, date(2026, 9, 1))
