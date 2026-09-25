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
    ACTIVE_ETF_HOLDING_CHANGE_DATASET,
    ACTIVE_ETF_HOLDING_DATASET,
    ETF_EXPENSE_RATIO_DATASET,
    ETF_NAV_DATASET,
    INSTITUTIONAL_FLOW_DATASET,
    PRICE_EOD_DATASET,
    US_ANALYST_CONSENSUS_DATASET,
    US_INSIDER_ACTIVITY_DATASET,
    US_INSTITUTIONAL_HOLDERS_DATASET,
    US_PRICE_EOD_DATASET,
    US_SECTOR_CLASS_DATASET,
    job_scoring_batch,
    job_trend_score_batch,
)
from db import models  # noqa: F401 — registers ORM models onto Base.metadata
from db.base import Base
from db.models import StockScoreRow, TrendScoreRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from ingest.context import IngestContext
from ingest.daemon import IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Market, Tier, TLSPolicy
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


def _seed_active_etf(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Two active-management ETFs ("00981A"/"00982A"-shaped symbols) with real `etf_nav`/
    `etf_expense_ratio`/`active_etf_holding`/`active_etf_holding_change` rows written through
    the REAL `ParquetStore` — a regression guard for the bug where `scoring_batch.py`'s own
    `_latest_etf_nav_by_symbol`/`_latest_etf_expense_ratio_by_symbol`/`_active_etf_symbols`/
    `_holdings_turnover_by_symbol` validated raw store reads directly against their
    `extra="forbid"` capability models: `ParquetStore.read_range`'s Hive-partitioned glob
    resurfaces the `dataset`/`date`/`v` bookkeeping columns, which `extra="forbid"` rejects,
    silently emptying every one of those four dicts (caught only by a bare `except ValueError:
    continue`) — so premium_discount/cost/management_activity were unavailable for EVERY ETF
    regardless of real data being present.

    Two symbols, not one: `_cost_factor`/`_management_activity_factor` are cross-sectional
    percentiles (`winsorized_percentile` returns `None` below 2 points), so a single-ETF universe
    would still show those two factors unavailable for a reason unrelated to the bug being
    guarded against — see each factor's own "數量不足2檔" unavailable-reason string.

    Each symbol needs its own 260-day price window (the ETF factor branch still needs
    momentum/flow like any other symbol) — reuses `_flat_then_ramp_closes` for that, tagged with
    an "00XXXA"-shaped symbol so `classify_symbol_type` routes both to the ETF branch."""
    window = calendar.business_days_between(
        calendar.shift_business_days(_RUN_DATE, -259), _RUN_DATE
    )
    symbols = ("00981A", "00982A")
    closes_by_symbol = {
        symbol: _flat_then_ramp_closes(window, final_return=final_return)
        for symbol, final_return in zip(symbols, (0.05, 0.08), strict=True)
    }
    for i, day in enumerate(window):
        rows = [
            row
            for symbol in symbols
            for row in _price_rows_for(symbol, [closes_by_symbol[symbol][i]], [day])
        ]
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)

    nav_day = window[-1]
    store.write_partition(
        ETF_NAV_DATASET,
        "TW",
        nav_day,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "as_of": nav_day.isoformat(),
                "fund_name": f"測試主動式ETF {symbol}",
                "units_outstanding": 1_000_000,
                "units_outstanding_change": 0,
                "close_price": 30.0,
                "estimated_nav": 29.9,
                "premium_discount_pct": premium_discount_pct,
                "prior_day_nav": 29.8,
            }
            for symbol, premium_discount_pct in zip(symbols, (0.33, -0.12), strict=True)
        ],
    )
    store.write_partition(
        ETF_EXPENSE_RATIO_DATASET,
        "TW",
        nav_day,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "as_of": nav_day.isoformat(),
                "management_fee_pct": 0.3,
                "custody_fee_pct": 0.035,
                "total_expense_ratio_pct_approx": total_expense_ratio_pct_approx,
            }
            for symbol, total_expense_ratio_pct_approx in zip(symbols, (0.335, 0.5), strict=True)
        ],
    )
    store.write_partition(
        ACTIVE_ETF_HOLDING_DATASET,
        "TW",
        nav_day,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "trade_date": nav_day.isoformat(),
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 1000,
                "weight": 10.0,
                "market_value": 1_000_000.0,
                "currency": "TWD",
            }
            for symbol in symbols
        ],
    )
    store.write_partition(
        ACTIVE_ETF_HOLDING_CHANGE_DATASET,
        "TW",
        nav_day,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "trade_date": nav_day.isoformat(),
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": buy,
                "sell": 0,
            }
            for symbol, buy in zip(symbols, (500, 900), strict=True)
        ],
    )


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


async def test_etf_factors_resolve_from_real_store_reads(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """Regression for the bookkeeping-column bug (see `_seed_active_etf`'s own docstring):
    `premium_discount`/`cost`/`management_activity` must actually resolve real percentiles, not
    fall back to `unavailable_reason`, once real `etf_nav`/`etf_expense_ratio`/
    `active_etf_holding`/`active_etf_holding_change` rows exist for the ETF in the read window."""
    ctx = _ctx(tmp_path, session_factory)
    _seed_universe(ctx.store, ctx.calendar)
    _seed_active_etf(ctx.store, ctx.calendar)
    await job_scoring_batch(_RUN_DATE, ctx)

    tool = build_registration_table(session_factory)[0][0]
    result = await dispatch_tool(
        tool,
        tool.input_model.model_validate({"id": "00981A", "market": "TW", "profile": "swing"}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    data = result.model_dump(mode="json")
    assert data["found"] is True
    breakdown = data["result"]["breakdown"]
    for factor_name in ("premium_discount", "cost", "management_activity"):
        assert breakdown[factor_name]["unavailable_reason"] is None, (
            f"{factor_name} unexpectedly unavailable: {breakdown[factor_name]}"
        )
        assert breakdown[factor_name]["percentile"] is not None


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


def _seed_us_universe(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """One US symbol (`AAPL`-shaped) plus SPY riding along in the same `us_price_eod` partition
    (`ingest/us_jobs.py::US_REFERENCE_INDEX_SYMBOL`'s own precedent — this mirrors that, not a
    fresh convention) — enough price history for momentum/trend_quality to resolve, and enough
    SPY history for `taiex_correlation` (the US branch reads SPY back via
    `analytics.scoring_batch._reference_index_closes`). No institutional-flow/market-value/ETF/
    stock-roster data is written at all — those factors/dimensions must degrade to unavailable
    for `Market.US`, not error, per `job_scoring_batch`'s own market-branching contract."""
    window = calendar.business_days_between(
        calendar.shift_business_days(_RUN_DATE, -259), _RUN_DATE
    )
    aapl_closes = _flat_then_ramp_closes(window, final_return=0.20)
    spy_closes = _flat_then_ramp_closes(window, final_return=0.05)
    for i, day in enumerate(window):
        rows = _price_rows_for("AAPL", [aapl_closes[i]], [day]) + _price_rows_for(
            "SPY", [spy_closes[i]], [day]
        )
        for row in rows:
            row["market"] = "US"
        store.write_partition(US_PRICE_EOD_DATASET, "US", day, rows)


def _seed_us_analyst_data(store: ParquetStore, run_date: date_) -> None:
    """The four US-native-factor datasets `ingest/us_jobs.py::job_us_fundamentals`'s extended
    fan-out writes — `us_analyst_consensus`/`us_institutional_holders`/`us_insider_activity`/
    `us_sector_class` — enough for AAPL's `analyst_revision`/`growth`/`institutional_ownership`/
    `insider_sentiment`/`sector_momentum` factors/dimensions to resolve to real values, proving
    the US-native replacement design genuinely computes (not just gracefully degrades).
    `current_year_growth_estimate`/`mean_target` are the two `growth` factor sub-signals."""
    store.write_partition(
        US_ANALYST_CONSENSUS_DATASET,
        "US",
        run_date,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "as_of": run_date.isoformat(),
                "eps_trend_current": 6.50,
                "eps_trend_90_days_ago": 6.00,
                "eps_revisions_up_last_30_days": 8,
                "eps_revisions_down_last_30_days": 2,
                "current_year_growth_estimate": 0.12,
                "mean_target": 220.0,
            },
            # SPY also gets a consensus row — `growth`'s two sub-signals
            # (`current_year_growth_estimate`/`price_target_upside`) are cross-sectional
            # percentiles (`winsorized_percentile`'s own >= 2-point floor), so a single-symbol
            # universe would leave `growth` unavailable for a reason unrelated to what this test
            # is proving.
            {
                "symbol": "SPY",
                "market": "US",
                "as_of": run_date.isoformat(),
                "current_year_growth_estimate": 0.08,
                "mean_target": 480.0,
            },
        ],
    )
    store.write_partition(
        US_INSTITUTIONAL_HOLDERS_DATASET,
        "US",
        run_date,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "holder_type": "institutional",
                "holder_name": "Example Asset Management",
                "date_reported": run_date.isoformat(),
                "pct_held": 6.5,
                "pct_change": 1.2,
            }
        ],
    )
    store.write_partition(
        US_INSIDER_ACTIVITY_DATASET,
        "US",
        run_date,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "insider_name": "Example Officer",
                "transaction": "Buy",
                "value": 500_000.0,
                "start_date": run_date.isoformat(),
            }
        ],
    )
    store.write_partition(
        US_SECTOR_CLASS_DATASET,
        "US",
        run_date,
        [
            {"symbol": "AAPL", "market": "US", "sector": "Technology", "sector_key": "technology"},
            # SPY is priced-only (an ETF, not a real "sector"), but `sector_momentum_trend_score`
            # needs >= 2 sectors in the day's comparison universe to rank against
            # (`winsorized_percentile`'s own floor) — giving it a distinct slug here is a test-only
            # convenience to exercise the cross-sectional ranking path, not a claim SPY "belongs"
            # to a real sector.
            {"symbol": "SPY", "market": "US", "sector": "Diversified", "sector_key": "diversified"},
        ],
    )


def _us_ctx(root: Path, session_factory: async_sessionmaker) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this job makes no FinMind call for US
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        us_calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        market=Market.US,
        session_factory=session_factory,
    )


async def test_us_scoring_batch_computes_momentum_and_analyst_revision(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """`Market.US`-scoped `job_scoring_batch`: momentum/trend_quality/liquidity must resolve from
    real price history, AND `analyst_revision`/`growth` (the two US-native factors replacing/
    adding to TW's five — see `UsStockScoreBreakdown`'s own docstring) must resolve from the
    seeded `us_analyst_consensus` row — never left permanently unavailable. `value_guard`/
    `quality` still degrade (no US financials seeded in this test). Critically: `revenue_flow_*`
    columns must stay `None` for a US row (that TW-only factor is never computed for `Market.US`
    at all — not computed-then-unavailable, genuinely never attempted)."""
    ctx = _us_ctx(tmp_path, session_factory)
    _seed_us_universe(ctx.store, ctx.calendar)
    _seed_us_analyst_data(ctx.store, _RUN_DATE)
    await job_scoring_batch(_RUN_DATE, ctx)

    async with session_factory() as session:
        rows = (await session.scalars(select(StockScoreRow))).all()

    us_rows = [row for row in rows if row.market == "US"]
    symbols = {row.symbol for row in us_rows}
    # SPY IS scored too, unlike TAIEX on the TW side — `scoreable_symbol_mask`'s TW branch
    # structurally excludes the non-numeric `TAIEX` label, but the US branch is unconditionally
    # permissive (see that function's own docstring: no warrant/derivative pollution to exclude
    # for a screener-discovered US universe), and SPY is a real, ordinary US ticker with no
    # special-cased exclusion anywhere in `job_scoring_batch`. This is correct, not a bug — SPY
    # riding along in the price partition as the reference-index proxy doesn't mean it should be
    # hidden from scoring too.
    assert symbols == {"AAPL", "SPY"}

    swing_rows = [row for row in us_rows if row.profile == "swing" and row.symbol == "AAPL"]
    assert len(swing_rows) == 1
    swing_row = swing_rows[0]
    assert swing_row.momentum_percentile is not None
    assert swing_row.momentum_unavailable_reason is None

    assert swing_row.analyst_revision_percentile is not None
    assert swing_row.analyst_revision_unavailable_reason is None

    assert swing_row.growth_percentile is not None
    assert swing_row.growth_unavailable_reason is None

    assert swing_row.liquidity_percentile is not None
    assert swing_row.liquidity_unavailable_reason is None

    # The TW-only `revenue_flow` slot is never touched for a US row — permanently null, not a
    # disclosed-unavailable computation.
    assert swing_row.revenue_flow_percentile is None
    assert swing_row.revenue_flow_unavailable_reason is None


async def test_us_trend_score_batch_computes_all_six_us_native_dimensions(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    """`Market.US`-scoped `job_trend_score_batch`: all six US-native dimensions
    (`technical`/`institutional_ownership`/`insider_sentiment`/`analyst_revision`/
    `sector_momentum`/`taiex_correlation`) must resolve to real values from the seeded price +
    `us_institutional_holders`/`us_insider_activity`/`us_analyst_consensus`/`us_sector_class`
    data — proving the US-native replacement design genuinely computes, not just gracefully
    degrades. The four TW-only columns (`chip_flow`/`margin_credit`/`broker_concentration`/
    `sector_rotation`) must stay permanently `None` — never computed for `Market.US` at all."""
    ctx = _us_ctx(tmp_path, session_factory)
    _seed_us_universe(ctx.store, ctx.calendar)
    _seed_us_analyst_data(ctx.store, _RUN_DATE)
    await job_trend_score_batch(_RUN_DATE, ctx)

    async with session_factory() as session:
        rows = (await session.scalars(select(TrendScoreRow))).all()
    us_rows = [row for row in rows if row.market == "US"]
    # SPY is scored too — see `test_us_scoring_batch_computes_momentum_and_analyst_revision`'s
    # own comment for why (unconditionally permissive `scoreable_symbol_mask` for US, no
    # special-cased exclusion of the reference-index symbol from scoring itself).
    assert {row.symbol for row in us_rows} == {"AAPL", "SPY"}
    row = next(r for r in us_rows if r.symbol == "AAPL")

    assert row.technical_value is not None
    assert row.technical_unavailable_reason is None
    assert row.taiex_correlation_value is not None
    assert row.taiex_correlation_unavailable_reason is None

    assert row.institutional_ownership_value is not None
    assert row.institutional_ownership_unavailable_reason is None
    assert row.insider_sentiment_value is not None
    assert row.insider_sentiment_unavailable_reason is None
    assert row.analyst_revision_value is not None
    assert row.analyst_revision_unavailable_reason is None
    assert row.sector_momentum_value is not None
    assert row.sector_momentum_unavailable_reason is None

    # The four TW-only dimensions are never computed for `Market.US` — permanently null columns,
    # not a disclosed-unavailable computation.
    assert row.chip_flow_value is None
    assert row.chip_flow_unavailable_reason is None
    assert row.margin_credit_value is None
    assert row.margin_credit_unavailable_reason is None
    assert row.broker_concentration_value is None
    assert row.broker_concentration_unavailable_reason is None
    assert row.sector_rotation_value is None
    assert row.sector_rotation_unavailable_reason is None
