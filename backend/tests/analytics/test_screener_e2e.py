"""End-to-end proofs for the tag screener — the three real call paths, not isolated units:

1. The batch job, driven through the REAL ingest daemon pass (not called directly), reads the
   price/institutional-flow partitions a prior write landed and writes a real `screener_flags`
   partition.
2. The read-time seam reads that partition back and computes correct AND/OR matches with correct
   `matched_tags`.
3. The `run_tag_screener` tool, driven through the REAL `dispatch_tool()` (not `.run()`), returns
   the same correct result.

Symbols are real-shaped Taiwan codes (four digits): the batch reads only scoreable symbols (equities
and ETFs, not the warrants that are ~90% of the raw partition), so a made-up name such as
"BREAKOUT" would be filtered out exactly as a warrant is. 2330 breaks out above its 20-day MA, 2454
has a foreign-investor net-buy streak, 2317 matches nothing.

No live network: price/flow history is written directly through the real `ParquetStore`, exactly
the recorded-fixture-shaped technique the ingest daemon's own end-to-end test uses.
"""

from __future__ import annotations

import threading
from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from analytics.screener import batch as batch_module
from analytics.screener.batch import (
    INSTITUTIONAL_FLOW_DATASET,
    PRICE_EOD_DATASET,
    SCREENER_FLAGS_DATASET,
    job_screener_flags,
)
from analytics.screener.store import run_tag_screener_query
from ingest.context import IngestContext
from ingest.daemon import IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from schemas.enums import Market, Tier, TLSPolicy
from schemas.screener import TagCondition, TagScreenerQuery
from tools.base import ToolContext, dispatch_tool
from tools.registry import ToolRegistry
from tools.screener_tools import build_registration_table

_TAIPEI = ZoneInfo("Asia/Taipei")
_RUN_DATE = date_(2024, 1, 2)  # a Tuesday, a trading day


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


def _price_rows_with_volume(
    symbol: str, closes: list[float], volumes: list[int], dates: list[date_]
) -> list[dict[str, object]]:
    """Same shape as `_price_rows_for`, but with a per-day volume series instead of the fixed
    1000 — needed to engineer a `volume_surge` fixture."""
    return [
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": day.isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
            "turnover": None,
        }
        for close, volume, day in zip(closes, volumes, dates, strict=True)
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


def _seed_upstream(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Write price + institutional-flow history across the trailing window, per-date, exactly as
    the real whole-market jobs would have. `BREAKOUT` closes flat below its 20-day MA then jumps
    above on the run date; `FOREIGN` is foreign-net-buy 3 days running; `NEITHER` matches nothing.
    """
    window = calendar.business_days_between(calendar.shift_business_days(_RUN_DATE, -89), _RUN_DATE)

    # BREAKOUT: flat at 100 for the whole window, then the final bar jumps to 130 (crosses its
    # 20-day MA up) — matches breakout_above_ma20.
    breakout_closes = [100.0] * (len(window) - 1) + [130.0]
    # NEITHER: a steady, unremarkable uptrend with no cross/flip on the final bar.
    neither_closes = [float(50 + i) for i in range(len(window))]

    for i, day in enumerate(window):
        price_rows = _price_rows_for("2330", [breakout_closes[i]], [day]) + _price_rows_for(
            "2317", [neither_closes[i]], [day]
        )
        store.write_partition(PRICE_EOD_DATASET, "TW", day, price_rows)

    # Five days of flow so the 5-day cumulative tag is evaluable. FOREIGN is a positive net every
    # day (so it hits both the 3-day streak and the 5-day cumulative). NEITHER matches nothing: its
    # last 3 days are not a one-directional streak and its 5-day sum is negative.
    chip_window = window[-5:]
    neither_nets = [200.0, -100.0, 400.0, -500.0, -300.0]
    for i, day in enumerate(chip_window):
        flow_rows = _flow_rows_for("2454", "Foreign_Investor", [500.0], [day]) + _flow_rows_for(
            "2317", "Foreign_Investor", [neither_nets[i]], [day]
        )
        store.write_partition(INSTITUTIONAL_FLOW_DATASET, "TW", day, flow_rows)


def _ctx(root: Path) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this job makes no FinMind call
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


# The 11 tag keys this dispatch added (KD crosses + the candlestick subset), so the end-to-end
# assertion can pin exactly which NEW tags a hand-engineered symbol triggers.
_NEW_TAGS = frozenset(
    {
        "kd_golden_cross",
        "kd_death_cross",
        "bullish_engulfing",
        "bearish_engulfing",
        "bullish_cannon",
        "bearish_cannon",
        "v_reversal",
        "inverse_v_reversal",
        "three_rising",
        "three_falling",
        "doji",
    }
)


def _ohlc_row(
    symbol: str, day: date_, ohlc: tuple[float, float, float, float]
) -> dict[str, object]:
    o, h, low, c = ohlc
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": day.isoformat(),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": 1000,
        "turnover": None,
    }


def _pattern_ohlc_for_day(day: date_, offset_from_end: int) -> dict[str, object]:
    """One PATTERN bar. The series is flat at 100 (open=high=low=close) for the whole window
    except the final five bars, which form a V bottom whose three-bar recovery is three rising
    green candles. That single symbol is engineered to trigger `v_reversal`, `three_rising`, and
    `kd_golden_cross` together, and no other new tag — a real multi-pattern reachability probe."""
    # offset_from_end: 0 == the run-date bar, 1 == the day before it, etc.
    tail = {
        4: (100.0, 100.0, 98.0, 98.0),  # red down bar, low 98
        3: (98.0, 98.0, 96.0, 96.0),  # red down bar, low 96
        2: (94.5, 95.5, 94.0, 95.0),  # green, pivot low 94, small body
        1: (96.0, 97.5, 96.0, 97.0),  # green, low 96, closes higher
        0: (98.0, 99.5, 98.0, 99.0),  # green, low 98, closes higher — completes V + three-rising
    }
    if offset_from_end in tail:
        return _ohlc_row("2412", day, tail[offset_from_end])
    return _ohlc_row("2412", day, (100.0, 100.0, 100.0, 100.0))


def _seed_with_pattern(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Seed the same BREAKOUT/NEITHER/FOREIGN symbols `_seed_upstream` does, plus a PATTERN symbol
    with real OHLC engineered to trigger three of the new tags — all written per-date in one call
    per partition so no symbol's rows clobber another's."""
    window = calendar.business_days_between(calendar.shift_business_days(_RUN_DATE, -89), _RUN_DATE)
    breakout_closes = [100.0] * (len(window) - 1) + [130.0]
    neither_closes = [float(50 + i) for i in range(len(window))]
    last = len(window) - 1
    for i, day in enumerate(window):
        rows = (
            _price_rows_for("2330", [breakout_closes[i]], [day])
            + _price_rows_for("2317", [neither_closes[i]], [day])
            + [_pattern_ohlc_for_day(day, last - i)]
        )
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)

    chip_window = window[-5:]
    neither_nets = [200.0, -100.0, 400.0, -500.0, -300.0]
    for i, day in enumerate(chip_window):
        flow_rows = _flow_rows_for("2454", "Foreign_Investor", [500.0], [day]) + _flow_rows_for(
            "2317", "Foreign_Investor", [neither_nets[i]], [day]
        )
        store.write_partition(INSTITUTIONAL_FLOW_DATASET, "TW", day, flow_rows)


# The 熱門/智能 tag keys this dispatch added, so the end-to-end assertion can pin exactly which
# NEW tags a hand-engineered symbol triggers.
_HOT_AND_SMART_TAGS = frozenset(
    {
        "volume_surge",
        "new_20d_high",
        "new_52w_high",
        "new_52w_low",
        "smart_breakout_volume",
        "smart_reversal_chip",
        "smart_trend_continuation",
        "smart_new_high_chip",
        "smart_foreign_buy_20d_high",
    }
)


def _seed_with_hot_and_smart(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Seed the same BREAKOUT/NEITHER/FOREIGN symbols `_seed_upstream` does, plus a SMART_COMBO
    symbol whose final bar is BOTH a breakout above its 20-day MA (like BREAKOUT) AND a volume
    spike at least 2x its trailing-20-day average volume on that SAME day — engineered so both
    ingredients of `smart_breakout_volume` (`breakout_above_ma20` AND `volume_surge`) are true
    simultaneously, proving the AND-combo genuinely requires both, not either. BREAKOUT keeps its
    flat volume (no surge) so it stays a negative control: it satisfies the breakout half but never
    the combo."""
    window = calendar.business_days_between(calendar.shift_business_days(_RUN_DATE, -89), _RUN_DATE)
    breakout_closes = [100.0] * (len(window) - 1) + [130.0]
    neither_closes = [float(50 + i) for i in range(len(window))]
    # SMART_COMBO: identical close path to BREAKOUT (flat at 100, jumps to 130 on the final bar,
    # crossing its 20-day MA), but volume is flat at 1000 for every day except the final bar, which
    # jumps to 3000 (3x the trailing-20-day average of 1000) — a volume surge on the exact day of
    # the breakout.
    smart_volumes = [1000] * (len(window) - 1) + [3000]

    for i, day in enumerate(window):
        rows = (
            _price_rows_for("2330", [breakout_closes[i]], [day])
            + _price_rows_for("2317", [neither_closes[i]], [day])
            + _price_rows_with_volume("2603", [breakout_closes[i]], [smart_volumes[i]], [day])
        )
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)

    chip_window = window[-5:]
    neither_nets = [200.0, -100.0, 400.0, -500.0, -300.0]
    for i, day in enumerate(chip_window):
        flow_rows = _flow_rows_for("2454", "Foreign_Investor", [500.0], [day]) + _flow_rows_for(
            "2317", "Foreign_Investor", [neither_nets[i]], [day]
        )
        store.write_partition(INSTITUTIONAL_FLOW_DATASET, "TW", day, flow_rows)


async def test_daemon_pass_triggers_hot_and_smart_tags_end_to_end(tmp_path: Path) -> None:
    """Drive the REAL daemon pass over a fixture whose SMART_COMBO symbol satisfies BOTH of
    `smart_breakout_volume`'s underlying conditions on the same day, proving: (1) the 熱門
    `volume_surge` tag is reachable through the real batch job, (2) the 智能 combo tag fires when
    both ingredients are true together, and (3) it does NOT fire for BREAKOUT, which has the
    breakout half but no volume surge — the dual-condition requirement is genuine, not vacuous."""
    ctx = _ctx(tmp_path)
    _seed_with_hot_and_smart(ctx.store, ctx.calendar)

    jobs = (ScheduledIngestJob("screener_flags", time_(20, 30), job_screener_flags),)
    daemon = IngestDaemon(ctx, jobs=jobs)
    ran = await daemon.run_due_jobs(now=datetime(2024, 1, 2, 20, 35, tzinfo=_TAIPEI))
    assert ran == ["screener_flags"]

    rows = ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _RUN_DATE)
    tags_by_symbol: dict[str, set[str]] = {}
    for row in rows:
        tags_by_symbol.setdefault(str(row["symbol"]), set()).add(str(row["tag"]))

    smart_combo_new_tags = tags_by_symbol.get("2603", set()) & _HOT_AND_SMART_TAGS
    assert smart_combo_new_tags == {"volume_surge", "new_20d_high", "smart_breakout_volume"}
    assert "breakout_above_ma20" in tags_by_symbol.get("2603", set())

    # BREAKOUT satisfies the breakout half only (flat volume, no surge) — the combo must not fire.
    assert "breakout_above_ma20" in tags_by_symbol.get("2330", set())
    # It is also a 20-day high (flat 100 then 130) but has no volume surge and no foreign
    # streak.
    assert tags_by_symbol.get("2330", set()) & _HOT_AND_SMART_TAGS == {"new_20d_high"}

    # No regression: Dispatch 1/2's tags still fire in the same run.
    assert "foreign_net_buy_streak_3" in tags_by_symbol.get("2454", set())
    # NEITHER rises monotonically, so its only flag is the 20-day high.
    assert {r["tag"] for r in rows if r["symbol"] == "2317"} <= {"new_20d_high"}


async def test_daemon_pass_triggers_new_tags_end_to_end(tmp_path: Path) -> None:
    """The failure-mode guard: drive the REAL daemon pass over a hand-engineered fixture and prove
    the newly-added tags are genuinely reachable through the batch job (not merely correct in
    isolated unit tests), while Dispatch 1's tags keep working in the SAME run (no regression)."""
    ctx = _ctx(tmp_path)
    _seed_with_pattern(ctx.store, ctx.calendar)

    jobs = (ScheduledIngestJob("screener_flags", time_(20, 30), job_screener_flags),)
    daemon = IngestDaemon(ctx, jobs=jobs)
    ran = await daemon.run_due_jobs(now=datetime(2024, 1, 2, 20, 35, tzinfo=_TAIPEI))
    assert ran == ["screener_flags"]

    rows = ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _RUN_DATE)
    tags_by_symbol: dict[str, set[str]] = {}
    for row in rows:
        tags_by_symbol.setdefault(str(row["symbol"]), set()).add(str(row["tag"]))

    # The engineered symbol triggers exactly these NEW tags — reachable through the real batch job.
    pattern_new_tags = tags_by_symbol.get("2412", set()) & _NEW_TAGS
    assert pattern_new_tags == {"v_reversal", "three_rising", "kd_golden_cross"}

    # No regression: Dispatch 1's technical/chip tags still fire in the same run.
    assert "breakout_above_ma20" in tags_by_symbol.get("2330", set())
    assert "foreign_net_buy_streak_3" in tags_by_symbol.get("2454", set())
    # The flat/close-only Dispatch 1 symbols carry no candlestick-shape tag — those need a real
    # open != close body, which close-only bars never have (a KD cross on BREAKOUT's jump is a
    # legitimate momentum event, not a spurious candlestick match, so it's not excluded here).
    candlestick_tags = _NEW_TAGS - {"kd_golden_cross", "kd_death_cross"}
    assert not (tags_by_symbol.get("2330", set()) & candlestick_tags)
    # NEITHER rises monotonically, so its only flag is the 20-day high.
    assert {r["tag"] for r in rows if r["symbol"] == "2317"} <= {"new_20d_high"}


async def test_daemon_pass_writes_flag_partition(tmp_path: Path) -> None:
    """Proof 1: the daemon fires the REAL batch job through to a landed `screener_flags`
    partition — not just that its schedule bookkeeping looks right."""
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)

    jobs = (ScheduledIngestJob("screener_flags", time_(20, 30), job_screener_flags),)
    daemon = IngestDaemon(ctx, jobs=jobs)

    ran = await daemon.run_due_jobs(now=datetime(2024, 1, 2, 20, 35, tzinfo=_TAIPEI))

    assert ran == ["screener_flags"]
    assert ctx.store.has_partition(SCREENER_FLAGS_DATASET, "TW", _RUN_DATE)
    rows = ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _RUN_DATE)
    flags = {(r["symbol"], r["tag"]) for r in rows}
    assert ("2330", "breakout_above_ma20") in flags
    assert ("2454", "foreign_net_buy_streak_3") in flags
    # NEITHER carries no flag at all, so it never appears in the tidy/long partition.
    # NEITHER rises monotonically, so its only flag is the 20-day high.
    assert {r["tag"] for r in rows if r["symbol"] == "2317"} <= {"new_20d_high"}


async def test_seam_reads_back_and_and_or(tmp_path: Path) -> None:
    """Proof 2: the read seam reads the landed partition and computes AND/OR with matched_tags."""
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)
    await job_screener_flags(_RUN_DATE, ctx)

    reader = ParquetMarketDataReader(root=tmp_path)

    # OR over two single-symbol tags: both symbols come back, each with only its own matched tag.
    or_query = TagScreenerQuery(
        tags=(
            TagCondition(tag="breakout_above_ma20"),
            TagCondition(tag="foreign_net_buy_streak_3"),
        ),
        match="OR",
    )
    or_result = run_tag_screener_query(reader, or_query, root=reader.root)
    assert or_result.total_matches == 2
    by_symbol = {hit.symbol: set(hit.matched_tags) for hit in or_result.hits}
    assert by_symbol == {
        "2330": {"breakout_above_ma20"},
        "2454": {"foreign_net_buy_streak_3"},
    }
    # `as_of` was resolved from the latest partition even though the query left it unset.
    assert or_result.as_of == _RUN_DATE

    # AND over the same two tags: no single symbol satisfies both, so zero matches.
    and_query = or_query.model_copy(update={"match": "AND"})
    and_result = run_tag_screener_query(reader, and_query, root=reader.root)
    assert and_result.total_matches == 0
    assert and_result.detail  # never empty

    # AND where a symbol genuinely satisfies both requested tags reports both in matched_tags.
    both_query = TagScreenerQuery(
        tags=(
            TagCondition(tag="foreign_net_buy_streak_3"),
            TagCondition(tag="foreign_net_buy_5d_cumulative"),
        ),
        match="AND",
    )
    both_result = run_tag_screener_query(reader, both_query, root=reader.root)
    assert both_result.total_matches == 1
    assert both_result.hits[0].symbol == "2454"
    assert set(both_result.hits[0].matched_tags) == {
        "foreign_net_buy_streak_3",
        "foreign_net_buy_5d_cumulative",
    }


async def test_seam_symbols_filter_narrows_hits(tmp_path: Path) -> None:
    """`TagScreenerQuery.symbols` narrows the match to just the requested symbols — a
    stock-detail badge asking "does this ONE symbol carry this tag" without paging through the
    whole market's `size`-capped hit list."""
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)
    await job_screener_flags(_RUN_DATE, ctx)

    reader = ParquetMarketDataReader(root=tmp_path)

    or_query = TagScreenerQuery(
        tags=(
            TagCondition(tag="breakout_above_ma20"),
            TagCondition(tag="foreign_net_buy_streak_3"),
        ),
        match="OR",
        symbols=("2454",),
    )
    result = run_tag_screener_query(reader, or_query, root=reader.root)
    assert result.total_matches == 1
    assert result.hits[0].symbol == "2454"

    # A requested symbol that isn't in the whole-market match set at all -> zero hits, never an
    # error.
    none_query = or_query.model_copy(update={"symbols": ("NOPE",)})
    none_result = run_tag_screener_query(reader, none_query, root=reader.root)
    assert none_result.total_matches == 0


async def test_tool_via_dispatch_tool(tmp_path: Path) -> None:
    """Proof 3: the tool driven through the REAL dispatch_tool returns the same correct result."""
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)
    await job_screener_flags(_RUN_DATE, ctx)

    reader = ParquetMarketDataReader(root=tmp_path)
    registry = ToolRegistry()
    for tool, immediate in build_registration_table(reader):
        registry.register(tool, immediate=immediate)

    tool = registry.get_tool("run_tag_screener")
    assert tool is not None

    result = await dispatch_tool(
        tool,
        tool.input_model.model_validate(
            {
                "market": Market.TW,
                "tags": [{"tag": "breakout_above_ma20"}],
                "match": "AND",
            }
        ),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    data = result.model_dump()
    hits = data["result"]["hits"]
    assert data["result"]["total_matches"] == 1
    assert hits[0]["symbol"] == "2330"
    assert hits[0]["matched_tags"] == ("breakout_above_ma20",)

    # The catalog tool is reachable through the same registry.
    catalog_tool = registry.get_tool("get_tag_catalog")
    assert catalog_tool is not None
    catalog = await dispatch_tool(
        catalog_tool,
        catalog_tool.input_model.model_validate({}),
        ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )
    categories = {c["category"] for c in catalog.model_dump()["categories"]}
    assert "技術面" in categories
    assert "籌碼面" in categories
    assert "熱門" in categories
    assert "智能" in categories


async def test_warrants_are_left_out_and_every_other_flag_is_unchanged(tmp_path: Path) -> None:
    """The price window used to be read whole, as ~5.7M dicts (about 6 GB on real data), 90% of
    them warrants. Now DuckDB drops the warrants while reading. This proves the flags for real
    symbols are exactly what they were, a warrant carries none, and a missing price value stays
    `None` (a NaN would pass `close <= 0`)."""
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)
    window = ctx.calendar.business_days_between(
        ctx.calendar.shift_business_days(_RUN_DATE, -89), _RUN_DATE
    )
    for day in window:  # a warrant that would break out, and a real stock with a missing close
        ctx.store.write_partition(
            PRICE_EOD_DATASET,
            "TW",
            day,
            [
                *ctx.store.read_partition(PRICE_EOD_DATASET, "TW", day),
                *_price_rows_for("030001", [1.0 if day != _RUN_DATE else 9.0], [day]),
            ],
        )

    await job_screener_flags(_RUN_DATE, ctx)

    flags = {
        (r["symbol"], r["tag"])
        for r in ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _RUN_DATE)
    }
    assert ("2330", "breakout_above_ma20") in flags
    assert ("2454", "foreign_net_buy_streak_3") in flags
    assert not any(symbol == "030001" for symbol, _ in flags)


async def test_the_job_runs_its_blocking_pass_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _seed_upstream(ctx.store, ctx.calendar)
    seen: list[int] = []
    original = batch_module._scoreable_price_rows

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(threading.get_ident())
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(batch_module, "_scoreable_price_rows", spy)

    await job_screener_flags(_RUN_DATE, ctx)

    assert seen
    assert threading.get_ident() not in seen
