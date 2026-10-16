"""`get_strategy_watch`: the watchlist joined with its latest check, the alert feed, Sponsor masking
for an unentitled caller, and the fallback to the last trading day's list."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from ingest.jobs.strategy_intraday import STRATEGY_HITS_DATASET
from ingest.store import ParquetStore
from schemas.enums import Tier
from tools.base import ToolContext
from tools.market.strategy_watch import GetStrategyWatchInput, GetStrategyWatchTool

_DAY = date(2026, 10, 16)


def _watch(symbol: str, strategy: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": {"2330": "台積電", "2409": "友達"}[symbol],
        "strategy": strategy,
        "close": 868.0,
        "upper_band": 870.0,
        "range_high_20d": 33.5,
        "large_holder_pct": 51.7,
        "days_on_list": 3,
        "regime": "neutral",
        "breadth_pct": 46.1,
        "as_of": "2026-10-15",
    }


def _hit(symbol: str, strategy: str, slot: str, *, alertable: bool) -> dict[str, object]:
    return {
        "symbol": symbol,
        "strategy": strategy,
        "slot": slot,
        "price": 872.0,
        "all_met": alertable,
        "alertable": alertable,
        "conditions": json.dumps(
            [
                {
                    "key": "price_above_upper_band",
                    "kind": "trigger",
                    "met": True,
                    "observed": 872.0,
                    "required": 870.0,
                }
            ]
        ),
    }


def _store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        STRATEGY_WATCHLIST_DATASET,
        "TW",
        _DAY,
        [_watch("2330", "volatility_squeeze"), _watch("2409", "chip_accumulation")],
    )
    store.write_partition(
        STRATEGY_HITS_DATASET,
        "TW",
        _DAY,
        [
            _hit("2330", "volatility_squeeze", "09:40", alertable=False),
            _hit("2330", "volatility_squeeze", "09:50", alertable=True),
            _hit("2330", "volatility_squeeze", "10:00", alertable=True),
            _hit("2330", "volatility_squeeze", "close", alertable=True),
        ],
    )
    return store


async def test_items_carry_the_latest_check_the_first_alert_and_the_close(tmp_path: Path) -> None:
    out = await GetStrategyWatchTool(_store(tmp_path)).run(
        GetStrategyWatchInput(as_of=_DAY), ToolContext(tier=Tier.PRO)
    )
    assert out.data_available is True
    assert out.last_slot == "10:00"
    assert out.counts["volatility_squeeze"] == 1 and out.counts["momentum_flag"] == 0
    tsmc = next(i for i in out.items if i.symbol == "2330")
    assert (tsmc.latest_slot, tsmc.alerted_at, tsmc.close_confirmed) == ("10:00", "09:50", True)
    assert tsmc.conditions[0].observed == 872.0
    auo = next(i for i in out.items if i.symbol == "2409")
    assert auo.latest_slot is None and auo.large_holder_pct == 51.7
    assert [(e.slot, e.kind) for e in out.events] == [("09:50", "intraday"), ("close", "close")]


async def test_an_unentitled_caller_gets_sponsor_values_masked(tmp_path: Path) -> None:
    out = await GetStrategyWatchTool(_store(tmp_path)).run(
        GetStrategyWatchInput(as_of=_DAY), ToolContext(tier=Tier.FREE)
    )
    assert out.masked is True
    tsmc = next(i for i in out.items if i.symbol == "2330")
    assert tsmc.conditions[0].met is True
    assert tsmc.conditions[0].observed is None and tsmc.price is None
    # A threshold can be an intraday value too (the open, the day's average price).
    assert tsmc.conditions[0].required is None
    assert next(i for i in out.items if i.symbol == "2409").large_holder_pct is None


async def test_a_weekend_shows_the_last_trading_days_list(tmp_path: Path) -> None:
    out = await GetStrategyWatchTool(_store(tmp_path)).run(
        GetStrategyWatchInput(as_of=date(2026, 10, 18)), ToolContext(tier=Tier.PRO)
    )
    assert out.run_date == _DAY


async def test_the_post_close_refresh_dates_skip_empty_days(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bar = {"symbol": "2330", "trade_date": "2026-10-15", "close": 860.0}
    store.write_partition("TaiwanStockPrice", "TW", date(2026, 10, 15), [bar])
    store.write_partition("TaiwanStockPrice", "TW", _DAY, [])  # a failed pull lands empty
    out = await GetStrategyWatchTool(store).run(
        GetStrategyWatchInput(as_of=_DAY), ToolContext(tier=Tier.PRO)
    )
    assert out.data_dates.price_eod == date(2026, 10, 15)
    assert out.data_dates.institutional_flow is None


async def test_no_list_is_reported_not_invented(tmp_path: Path) -> None:
    out = await GetStrategyWatchTool(ParquetStore(root=tmp_path)).run(
        GetStrategyWatchInput(as_of=_DAY), ToolContext(tier=Tier.PRO)
    )
    assert out.data_available is False
    assert out.unavailable_reason
    assert out.items == ()
