"""策略盯盤 intraday check: each strategy's triggers, the guards, and the two-slot confirmation."""

from __future__ import annotations

import json
from datetime import time as time_

from analytics.strategy_watch.intraday import evaluate_close, evaluate_slot, market_change, slot_of
from analytics.strategy_watch.strategies import Strategy

_SQUEEZE = {
    "symbol": "2330",
    "strategy": Strategy.VOLATILITY_SQUEEZE.value,
    "upper_band": 868.0,
    "close_5d_ago": 851.0,
}
_OVERSOLD = {"symbol": "2317", "strategy": Strategy.OVERSOLD_BOUNCE.value, "k": 15.0}
_DIVERGENCE = {
    "symbol": "2454",
    "strategy": Strategy.AD_DIVERGENCE.value,
    "low_20d": 1000.0,
    "rsi14": 38.0,
    "rsi_at_low": 31.0,
    "ad_value": 5.0,
    "ad_low_20d": 1.0,
}


def _quote(symbol: str, **fields: float) -> dict[str, object]:
    return {"symbol": symbol, "change_rate": 0.5, **fields}


def _keys(conditions: object) -> dict[str, bool]:
    return {c.key: c.met for c in conditions}  # type: ignore[attr-defined]


def test_squeeze_hit_lists_each_condition_with_observed_and_required_values() -> None:
    quote = _quote("2330", close=872, volume_ratio=2.45, average_price=865, change_rate=1.5)
    (hit,) = evaluate_slot([_SQUEEZE], [quote], slot=time_(9, 40), previously_met=frozenset())
    assert hit.all_met is True
    assert _keys(hit.conditions)["price_above_upper_band"] is True
    upper = next(c for c in hit.conditions if c.key == "price_above_upper_band")
    assert (upper.observed, upper.required) == (872, 868)
    # Met once is not enough: it must hold on two consecutive slots before an alert.
    assert hit.confirmed is False
    assert hit.alertable is False


def test_a_second_consecutive_slot_makes_the_hit_alertable() -> None:
    quote = _quote("2330", close=872, volume_ratio=2.45, average_price=865, change_rate=1.5)
    (hit,) = evaluate_slot(
        [_SQUEEZE],
        [quote],
        slot=time_(9, 50),
        previously_met=frozenset({("2330", Strategy.VOLATILITY_SQUEEZE.value)}),
    )
    assert hit.confirmed is True
    assert hit.alertable is True


def test_low_volume_breakout_is_a_near_miss() -> None:
    quote = _quote("2330", close=872, volume_ratio=1.1, average_price=865)
    (hit,) = evaluate_slot([_SQUEEZE], [quote], slot=time_(10, 0), previously_met=frozenset())
    assert hit.all_met is False
    assert _keys(hit.conditions)["volume_ratio_at_least"] is False


def test_guards_block_the_alert_but_not_the_log() -> None:
    met_before = frozenset({("2330", Strategy.VOLATILITY_SQUEEZE.value)})
    near_limit = _quote("2330", close=872, volume_ratio=2, average_price=860, change_rate=9.6)
    (hit,) = evaluate_slot([_SQUEEZE], [near_limit], slot=time_(10, 0), previously_met=met_before)
    assert hit.confirmed is True
    assert hit.alertable is False
    assert _keys(hit.conditions)["not_near_limit_up"] is False

    opening = _quote("2330", close=872, volume_ratio=2, average_price=860)
    (early,) = evaluate_slot([_SQUEEZE], [opening], slot=time_(9, 20), previously_met=met_before)
    assert early.alertable is False
    assert _keys(early.conditions)["after_opening_window"] is False


def test_a_falling_market_blocks_alerts() -> None:
    met_before = frozenset({("2330", Strategy.VOLATILITY_SQUEEZE.value)})
    falling = [_quote(f"{1000 + i}", close=10, change_rate=-2.0) for i in range(5)]
    quote = _quote("2330", close=872, volume_ratio=2, average_price=860, change_rate=-2.0)
    (hit,) = evaluate_slot(
        [_SQUEEZE], [quote, *falling], slot=time_(10, 0), previously_met=met_before
    )
    assert _keys(hit.conditions)["market_not_falling"] is False
    assert hit.alertable is False


def test_oversold_bounce_needs_a_red_candle_on_light_volume() -> None:
    quote = _quote("2317", close=101, open=99, volume_ratio=0.7, average_price=100)
    (hit,) = evaluate_slot([_OVERSOLD], [quote], slot=time_(10, 0), previously_met=frozenset())
    assert hit.all_met is True
    black = _quote("2317", close=98, open=99, volume_ratio=0.7)
    (miss,) = evaluate_slot([_OVERSOLD], [black], slot=time_(10, 0), previously_met=frozenset())
    assert _keys(miss.conditions)["red_candle"] is False


def test_ad_divergence_needs_a_new_low() -> None:
    below = _quote("2454", close=995)
    (hit,) = evaluate_slot([_DIVERGENCE], [below], slot=time_(10, 0), previously_met=frozenset())
    assert hit.all_met is True
    assert "above_average_price" not in _keys(hit.conditions)
    above = _quote("2454", close=1005)
    (miss,) = evaluate_slot([_DIVERGENCE], [above], slot=time_(10, 0), previously_met=frozenset())
    assert _keys(miss.conditions)["new_20d_low"] is False


def test_a_watchlist_stock_missing_from_the_snapshot_is_skipped() -> None:
    assert evaluate_slot([_SQUEEZE], [], slot=time_(10, 0), previously_met=frozenset()) == []


def test_slot_and_market_helpers() -> None:
    assert slot_of(time_(9, 47, 30)) == time_(9, 40)
    rows = [_quote("2330", change_rate=1.0), _quote("2317", change_rate=-3.0)]
    rows.append({"symbol": "00878", "change_rate": 9.0})  # an ETF code: not in the median
    assert market_change(rows) == -1.0


def test_hit_row_carries_conditions_as_json() -> None:
    quote = _quote("2330", close=872, volume_ratio=2.45, average_price=865)
    (hit,) = evaluate_slot([_SQUEEZE], [quote], slot=time_(9, 40), previously_met=frozenset())
    row = hit.to_row("2026-10-16")
    assert row["slot"] == "09:40"
    assert row["run_date"] == "2026-10-16"
    decoded = json.loads(str(row["conditions"]))
    assert decoded[0]["key"] == "price_above_upper_band"


_ACCUMULATION = {
    "symbol": "2409",
    "strategy": Strategy.CHIP_ACCUMULATION.value,
    "range_high_20d": 33.5,
    "avg_volume_20d": 40_000_000.0,
}


def test_chip_accumulation_breakout_needs_volume() -> None:
    strong = _quote("2409", close=33.9, volume_ratio=2.1, average_price=33.6)
    (hit,) = evaluate_slot([_ACCUMULATION], [strong], slot=time_(10, 0), previously_met=frozenset())
    assert hit.all_met is True
    assert "not_near_limit_up" in _keys(hit.conditions)
    quiet = _quote("2409", close=33.9, volume_ratio=1.0, average_price=33.6)
    (miss,) = evaluate_slot([_ACCUMULATION], [quiet], slot=time_(10, 0), previously_met=frozenset())
    assert miss.all_met is False


def test_close_confirms_a_breakout_on_volume() -> None:
    eod = [{"symbol": "2409", "close": 34.2, "volume": 70_000_000}]
    (hit,) = evaluate_close([_ACCUMULATION], eod)
    assert hit.slot == "close"
    assert hit.alertable is True
    assert _keys(hit.conditions) == {"close_above_range_high": True, "close_volume_multiple": True}

    thin = [{"symbol": "2409", "close": 34.2, "volume": 41_000_000}]  # about 1x average
    (miss,) = evaluate_close([_ACCUMULATION], thin)
    assert miss.alertable is False


def test_close_check_covers_breakouts_only() -> None:
    eod = [{"symbol": "2317", "close": 101, "volume": 9_000_000}]
    assert evaluate_close([_OVERSOLD], eod) == []


def test_momentum_flag_triggers_and_close_check_use_the_pause_high() -> None:
    watch = {
        "symbol": "3017",
        "strategy": Strategy.MOMENTUM_FLAG.value,
        "flag_high": 60.3,
        "avg_volume_20d": 2_000_000.0,
    }
    quote = _quote("3017", close=60.8, volume_ratio=1.9, average_price=60.5)
    (hit,) = evaluate_slot([watch], [quote], slot=time_(10, 0), previously_met=frozenset())
    assert _keys(hit.conditions)["price_above_flag_high"] is True
    assert hit.all_met is True
    (close,) = evaluate_close([watch], [{"symbol": "3017", "close": 61.0, "volume": 3_500_000}])
    assert _keys(close.conditions) == {"close_above_flag_high": True, "close_volume_multiple": True}
