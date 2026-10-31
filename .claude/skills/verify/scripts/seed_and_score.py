"""Seed a tiny synthetic TW market into the local Parquet store, then run the scoring batch once.

Run from the SCRATCH directory (never from backend/, whose .env holds live secrets) with
DATABASE_URL and PARQUET_ROOT exported, through the backend project:

    uv run --project <repo>/backend python <repo>/.claude/skills/verify/scripts/seed_and_score.py

Three symbols with hand-picked 12-1 momentum (9001 +30%, 9002 +10%, 9003 -10%) and a matching
foreign-investor flow tilt, so the leaderboard order is checkable by eye: 9001 > 9002 > 9003.
Optional: --monthly-revenue also writes monthly revenue so the revenue_flow factor's YoY half can
be exercised (needs SCORE_REVENUE_YOY_ENABLED=true in the environment to take effect).
"""

from __future__ import annotations

import argparse
import asyncio
from calendar.trading_days import TradingDayCalendar
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.fundamentals import MONTH_REVENUE_DATASET
from analytics.scoring_batch import INSTITUTIONAL_FLOW_DATASET, PRICE_EOD_DATASET, job_scoring_batch
from config.settings import get_settings
from db import models  # noqa: F401  registers ORM models onto Base.metadata
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from ingest.context import IngestContext
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy

RUN_DATE = date(2024, 1, 2)
PRICE_DAYS = 260
FLOW_DAYS = 20
SYMBOLS = {"9001": (0.30, 500.0), "9002": (0.10, 100.0), "9003": (-0.10, -300.0)}


def _closes(days: int, final_return: float) -> list[float]:
    closes = [100.0] * days
    for i in range(days - 1 - 21, days):
        closes[i] = 100.0 * (1.0 + final_return)
    return closes


def _seed_prices_and_flow(store: ParquetStore, window: list[date]) -> None:
    closes = {symbol: _closes(len(window), ret) for symbol, (ret, _) in SYMBOLS.items()}
    for i, day in enumerate(window):
        store.write_partition(
            PRICE_EOD_DATASET,
            "TW",
            day,
            [
                {
                    "symbol": symbol,
                    "market": "TW",
                    "trade_date": day.isoformat(),
                    "open": closes[symbol][i],
                    "high": closes[symbol][i],
                    "low": closes[symbol][i],
                    "close": closes[symbol][i],
                    "volume": 1000,
                    "turnover": None,
                }
                for symbol in SYMBOLS
            ],
        )
    for day in window[-FLOW_DAYS:]:
        store.write_partition(
            INSTITUTIONAL_FLOW_DATASET,
            "TW",
            day,
            [
                {
                    "symbol": symbol,
                    "market": "TW",
                    "trade_date": day.isoformat(),
                    "investor": "Foreign_Investor",
                    "buy": int(max(net, 0)),
                    "sell": int(max(-net, 0)),
                }
                for symbol, (_, net) in SYMBOLS.items()
            ],
        )


def _seed_monthly_revenue(store: ParquetStore) -> None:
    """Revenue for Nov and Dec 2023 plus the same two months of 2022, stored the way the ingest job
    stores them: under the 1st of the following month, before the 10th publication date.

    The run date is 2024-01-02, so November (published Dec 10) is knowable and December
    (published Jan 10) is not. November's YoY follows the momentum ordering (9001 best); December's
    is deliberately reversed (9003 best). With the publication gate the YoY half ranks 9001 > 9002 >
    9003; a look-ahead leak of December would flip it."""
    growth_by_month = {
        11: {"9001": 1.50, "9002": 1.10, "9003": 0.70},
        12: {"9001": 0.70, "9002": 1.10, "9003": 1.50},
    }
    for revenue_year, revenue_month, partition_day in (
        (2022, 11, date(2022, 12, 1)),
        (2022, 12, date(2023, 1, 2)),
        (2023, 11, date(2023, 12, 1)),
        (2023, 12, date(2024, 1, 2)),
    ):
        base = 1_000_000
        growth = growth_by_month[revenue_month]
        store.write_partition(
            MONTH_REVENUE_DATASET,
            "TW",
            partition_day,
            [
                {
                    "symbol": symbol,
                    "market": "TW",
                    "trade_date": partition_day.isoformat(),
                    "country": "Taiwan",
                    "revenue": int(base * (growth[symbol] if revenue_year == 2023 else 1.0)),
                    "revenue_month": revenue_month,
                    "revenue_year": revenue_year,
                }
                for symbol in SYMBOLS
            ],
        )


async def main(*, monthly_revenue: bool) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    calendar = TradingDayCalendar()
    store = ParquetStore(root=settings.parquet_root)
    window = calendar.business_days_between(
        calendar.shift_business_days(RUN_DATE, -(PRICE_DAYS - 1)), RUN_DATE
    )
    _seed_prices_and_flow(store, window)
    if monthly_revenue:
        _seed_monthly_revenue(store)

    ctx = IngestContext(
        finmind=None,  # type: ignore[arg-type]  # the scoring batch makes no FinMind call
        store=store,
        leader=AlwaysLeader(),
        calendar=calendar,
        tls_policy=TLSPolicy.STRICT,
        session_factory=session_factory,
    )
    run = await job_scoring_batch(RUN_DATE, ctx)
    print(f"scoring_batch status={run.status} rows_written={run.rows_written} as_of={RUN_DATE}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--monthly-revenue", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(monthly_revenue=args.monthly_revenue))
