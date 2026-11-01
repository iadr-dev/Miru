"""Seed a tiny synthetic TW market into the local Parquet store, then run the scoring batch once.

Run from the SCRATCH directory (never from backend/, whose .env holds live secrets) with
DATABASE_URL and PARQUET_ROOT exported, through the backend project:

    uv run --project <repo>/backend python <repo>/.claude/skills/verify/scripts/seed_and_score.py

Three symbols with hand-picked 12-1 momentum (9001 +30%, 9002 +10%, 9003 -10%) and a matching
foreign-investor flow tilt, so the leaderboard order is checkable by eye: 9001 > 9002 > 9003.
"""

from __future__ import annotations

import asyncio
from calendar.trading_days import TradingDayCalendar
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def main() -> None:
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
    asyncio.run(main())
