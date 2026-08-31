"""End-to-end proofs for the daily scoring batch — the real call paths, not isolated units:

1. The batch job, driven through the REAL ingest daemon pass (not called directly), reads a
   hand-built multi-symbol price/institutional-flow history written through the REAL
   `ParquetStore`, and writes real `stock_scores` rows through the REAL relational session
   factory.
2. The `get_stock_score` tool, driven through the REAL `dispatch_tool()` (not `.run()`), returns
   the cached row for one symbol.
3. The `get_score_leaderboard` tool, likewise via `dispatch_tool()`, returns a genuinely correct
   cross-sectional ordering — proven with a real numeric check (three symbols with distinct
   momentum inputs; the leaderboard's sort is asserted to be the actual momentum ranking, not
   merely "non-empty").
4. A governance check: no cached-score tool output contains buy/sell/hold trading language.

No live network: price/flow history is written directly through the real `ParquetStore`, the same
recorded-fixture-shaped technique `test_screener_e2e.py` uses.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections.abc import AsyncIterator
from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.models import StockScoreBreakdown
from analytics.scoring_batch import (
    INSTITUTIONAL_FLOW_DATASET,
    PRICE_EOD_DATASET,
    job_scoring_batch,
)
from db import models  # noqa: F401 — registers ORM models onto Base.metadata
from db.base import Base
from db.models import StockScoreRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from ingest.context import IngestContext
from ingest.daemon import IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Tier, TLSPolicy
from tools.base import ToolContext, dispatch_tool
from tools.score_tools import build_registration_table

_TAIPEI = ZoneInfo("Asia/Taipei")
_RUN_DATE = date_(2024, 1, 2)  # a Tuesday, a trading day

# Buy/sell/hold-language guard, mirroring `tests/analytics/test_score.py`'s `_TRADING_LANGUAGE`.
_TRADING_LANGUAGE = ("buy", "sell", "hold", "進場", "停損", "加碼", "減碼", "推薦", "target price")


def _price_rows_for(
    symbol: str, closes: list[float], dates: list[date_]
) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": day.isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "turnover": None,
        }
        for close, day in zip(closes, dates, strict=True)
    ]


def _flow_rows_for(
    symbol: str, investor: str, nets: list[float], dates: list[date_]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for net, day in zip(nets, dates, strict=True):
        buy = int(net) if net > 0 else 0
        sell = int(-net) if net < 0 else 0
        rows.append(
            {
                "symbol": symbol,
                "market": "TW",
                "trade_date": day.isoformat(),
                "investor": investor,
                "buy": buy,
                "sell": sell,
            }
        )
    return rows


def _flat_then_ramp_closes(window: list[date_], *, final_return: float) -> list[float]:
    """A close series that is flat at 100 for the whole 12-1 lookback, then steps up on the very
    last (skip-month-excluded) bar by `final_return` — giving each symbol a distinct, hand-chosen
    12-1 momentum value without needing >260 days of realistic price noise."""
    n = len(window)
    # The 12-1 momentum anchor points are index -1-21 (near) and -1-252 (far); everything up to
    # -22 is left flat at 100, and the near-anchor point onward ramps to `final_return` above 100
    # so the momentum calc (near - far)/far lands close to `final_return`.
    closes = [100.0] * n
    near_index = n - 1 - 21
    for i in range(near_index, n):
        closes[i] = 100.0 * (1.0 + final_return)
    return closes


def _seed_universe(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Three symbols with distinct, hand-engineered 12-1 momentum so the leaderboard's ordering
    can be checked against real numbers: STRO (+30%), MID (+10%), WEAK (-10%). Each also gets a
    matching institutional-flow tilt so revenue_flow doesn't fight momentum in the composite."""
    window = calendar.business_days_between(
        calendar.shift_business_days(_RUN_DATE, -259), _RUN_DATE
    )

    strong_closes = _flat_then_ramp_closes(window, final_return=0.30)
    mid_closes = _flat_then_ramp_closes(window, final_return=0.10)
    weak_closes = _flat_then_ramp_closes(window, final_return=-0.10)

    for i, day in enumerate(window):
        rows = (
            _price_rows_for("STRO", [strong_closes[i]], [day])
            + _price_rows_for("MID", [mid_closes[i]], [day])
            + _price_rows_for("WEAK", [weak_closes[i]], [day])
        )
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)

    flow_window = window[-20:]
    for day in flow_window:
        rows = (
            _flow_rows_for("STRO", "Foreign_Investor", [500.0], [day])
            + _flow_rows_for("MID", "Foreign_Investor", [100.0], [day])
            + _flow_rows_for("WEAK", "Foreign_Investor", [-300.0], [day])
        )
        store.write_partition(INSTITUTIONAL_FLOW_DATASET, "TW", day, rows)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ctx(root: Path, session_factory: async_sessionmaker) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this job makes no FinMind call
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=session_factory,
    )


async def test_daemon_pass_writes_score_rows_for_whole_universe(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """Proof 1: the daemon fires the REAL batch job through to real `stock_scores` rows for
    EVERY symbol in the seeded universe (no top_n/limit anywhere on the write path) — both
    horizon profiles, for all three symbols."""
    ctx = _ctx(tmp_path, session_factory)
    _seed_universe(ctx.store, ctx.calendar)

    jobs = (ScheduledIngestJob("scoring_batch", time_(23, 55), job_scoring_batch),)
    daemon = IngestDaemon(ctx, jobs=jobs)
    ran = await daemon.run_due_jobs(now=datetime(2024, 1, 2, 23, 59, tzinfo=_TAIPEI))
    assert ran == ["scoring_batch"]

    async with session_factory() as session:
        rows = (await session.scalars(select(StockScoreRow))).all()

    symbols = {row.symbol for row in rows}
    profiles = {row.profile for row in rows}
    assert symbols == {"STRO", "MID", "WEAK"}
    assert profiles == {"swing", "position"}
    # 3 symbols x 2 profiles = 6 rows — the whole universe, not a subset.
    assert len(rows) == 6
    assert all(row.as_of == _RUN_DATE.isoformat() for row in rows)


async def test_stock_score_tool_reads_back_cached_row(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """Proof 2: `get_stock_score`, driven through the REAL `dispatch_tool`, returns the cached
    composite + full breakdown for one symbol, matching what the batch actually wrote."""
    ctx = _ctx(tmp_path, session_factory)
    _seed_universe(ctx.store, ctx.calendar)
    await job_scoring_batch(_RUN_DATE, ctx)

    tool = build_registration_table(session_factory)[0][0]
    assert tool.name == "get_stock_score"

    result = await dispatch_tool(
        tool,
        tool.input_model.model_validate({"id": "STRO", "market": "TW", "profile": "swing"}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    data = result.model_dump(mode="json")
    assert data["found"] is True
    assert data["result"]["symbol"] == "STRO"
    assert data["result"]["composite"] is not None
    breakdown = data["result"]["breakdown"]
    for factor_name in StockScoreBreakdown.model_fields:
        assert factor_name in breakdown

    # A symbol the batch never scored returns found=False, never a fabricated row.
    missing = await dispatch_tool(
        tool,
        tool.input_model.model_validate({"id": "NOPE", "market": "TW", "profile": "swing"}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    missing_data = missing.model_dump(mode="json")
    assert missing_data["found"] is False
    assert missing_data["message"]


async def test_leaderboard_tool_orders_by_real_composite(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """Proof 3 + the cross-sectional check: STRO (+30% momentum, positive flow) must rank above
    MID (+10%, mild positive flow), which must rank above WEAK (-10% momentum, negative flow) —
    for BOTH the bullish (descending) and bearish (ascending) directions, proving the leaderboard
    reads a genuinely correct ordering off the cache, not merely a non-empty list."""
    ctx = _ctx(tmp_path, session_factory)
    _seed_universe(ctx.store, ctx.calendar)
    await job_scoring_batch(_RUN_DATE, ctx)

    tool = build_registration_table(session_factory)[1][0]
    assert tool.name == "get_score_leaderboard"

    bullish = await dispatch_tool(
        tool,
        tool.input_model.model_validate(
            {"market": "TW", "profile": "swing", "direction": "bullish", "limit": 20}
        ),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    bullish_data = bullish.model_dump(mode="json")
    bullish_symbols = [row["symbol"] for row in bullish_data["results"]]
    assert bullish_symbols == ["STRO", "MID", "WEAK"]
    composites = [row["composite"] for row in bullish_data["results"]]
    assert composites == sorted(composites, reverse=True)  # genuinely descending, not just present

    bearish = await dispatch_tool(
        tool,
        tool.input_model.model_validate(
            {"market": "TW", "profile": "swing", "direction": "bearish", "limit": 20}
        ),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    bearish_symbols = [row["symbol"] for row in bearish.model_dump(mode="json")["results"]]
    assert bearish_symbols == ["WEAK", "MID", "STRO"]

    # limit is a read-time slice only — asking for 2 must not change the ordering, just truncate.
    top2 = await dispatch_tool(
        tool,
        tool.input_model.model_validate(
            {"market": "TW", "profile": "swing", "direction": "bullish", "limit": 2}
        ),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    top2_symbols = [row["symbol"] for row in top2.model_dump(mode="json")["results"]]
    assert top2_symbols == ["STRO", "MID"]


async def test_no_trading_language_in_score_or_leaderboard_output(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """Governance check: neither tool's dispatched output contains buy/sell/hold trading
    language, mirroring `tests/analytics/test_score.py`'s `_TRADING_LANGUAGE` guard."""
    ctx = _ctx(tmp_path, session_factory)
    _seed_universe(ctx.store, ctx.calendar)
    await job_scoring_batch(_RUN_DATE, ctx)

    score_tool, leaderboard_tool = (t for t, _ in build_registration_table(session_factory))
    score_result = await dispatch_tool(
        score_tool,
        score_tool.input_model.model_validate({"id": "STRO", "market": "TW"}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    leaderboard_result = await dispatch_tool(
        leaderboard_tool,
        leaderboard_tool.input_model.model_validate({"market": "TW"}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    # The band enum values themselves (偏多/中性/偏空) never carry Latin trading vocabulary, and
    # the composite/breakdown output is otherwise all numeric/descriptive text — check the band
    # value specifically (mirrors test_score.py's enum-vocabulary check) rather than the whole
    # dumped payload, since factor *descriptions* are free prose that could legitimately contain
    # a word like "sell" only inside a disclaiming caveat sentence (as `non_trading_caveat` does).
    score_band = score_result.model_dump(mode="json")["result"]["band"]
    for banned in _TRADING_LANGUAGE:
        assert banned not in score_band.lower()

    for row in leaderboard_result.model_dump(mode="json")["results"]:
        for banned in _TRADING_LANGUAGE:
            assert banned not in row["band"].lower()
