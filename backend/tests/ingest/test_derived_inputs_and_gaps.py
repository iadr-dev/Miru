"""Derived jobs need their price input for the day, and an empty placeholder day is still a gap.

The 2026-10-14/15 symptom: scores and screener flags were computed for days with no price data,
from the newest older prices, and stored under the new day (so they looked done and were never
recomputed). And a day fetched before the upstream had published it was recorded as an empty
placeholder that the gap check then treated as filled.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

from analytics.scoring_batch import job_scoring_batch, job_trend_score_batch
from analytics.screener.batch import job_screener_flags
from config.settings import Settings
from ingest.backfill import detect_missing_trading_days
from ingest.context import IngestContext
from ingest.jobs import PRICE_EOD_JOB
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

_PRICE = "TaiwanStockPrice"
_DAY = date(2026, 10, 15)  # a Thursday


def _ctx(root: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(
            settings=Settings(
                finmind_api_key="test-token",
                finmind_base_url="https://api.finmindtrade.com/api/v4",
            )
        ),
        store=ParquetStore(root=root),  # type: ignore[arg-type]
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


@pytest.mark.parametrize("job", [job_scoring_batch, job_trend_score_batch, job_screener_flags])
async def test_a_derived_job_skips_a_day_with_no_price_data(tmp_path: object, job) -> None:
    ctx = _ctx(tmp_path)
    # Prices exist for an older day only, as when the price pull has not run for 2026-10-15.
    ctx.store.write_partition(_PRICE, "TW", date(2026, 9, 25), [{"symbol": "2330", "close": 1.0}])

    run = await job(_DAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.rows_written == 0
    assert "price data" in (run.detail or "")


async def test_a_derived_job_also_skips_when_the_day_is_only_an_empty_placeholder(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path)
    ctx.store.write_partition(_PRICE, "TW", _DAY, [])  # fetched before the upstream published

    run = await job_screener_flags(_DAY, ctx)

    assert run.status is RunStatus.SKIPPED


def test_has_rows_ignores_a_placeholder_and_sees_the_real_rows_that_replace_it(
    tmp_path: object,
) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(_PRICE, "TW", _DAY, [])

    assert store.has_partition(_PRICE, "TW", _DAY) is True
    assert store.has_rows(_PRICE, "TW", _DAY) is False
    store.list_partition_dates(_PRICE, "TW")  # warm the cached date list

    store.write_partition(_PRICE, "TW", _DAY, [{"symbol": "2330", "close": 1.0}])

    # The same store instance sees the replaced day at once (the cache is not left stale).
    assert store.has_rows(_PRICE, "TW", _DAY) is True
    assert _DAY in store.list_partition_dates(_PRICE, "TW")


def test_a_placeholder_day_is_still_reported_as_a_gap(tmp_path: object) -> None:
    ctx = _ctx(tmp_path)
    ctx.store.write_partition(_PRICE, "TW", date(2026, 10, 14), [{"symbol": "2330", "close": 1.0}])
    ctx.store.write_partition(_PRICE, "TW", _DAY, [])

    missing = detect_missing_trading_days(PRICE_EOD_JOB, ctx, start=date(2026, 10, 14), end=_DAY)

    assert missing == (_DAY,)
