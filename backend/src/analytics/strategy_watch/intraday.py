"""The intraday check: one whole-market snapshot compared against the morning watchlist.

Each watchlist row's strategy has trigger conditions (the levels recorded at 08:00) and guards (the
checks a practitioner applies before acting on any intraday signal). A hit is `all_met` when every
trigger holds, and `alertable` only when it also held on the previous check (two consecutive
10-minute slots, so one print through a level is not enough) and every guard passes. Everything
else is the near-miss log: written, shown on the page, never sent.

Pure functions: no I/O, so each condition is unit-tested on its own.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from datetime import time as time_
from typing import Literal

from pydantic import BaseModel, ConfigDict

from analytics.coerce import as_float
from analytics.strategy_watch.strategies import KD_OVERSOLD, Strategy

SLOT_MINUTES = 10
# The first half hour is the noisiest part of the session (overnight orders, opening auction
# imbalance); conditions are checked and logged but not alerted before this.
ALERT_FROM = time_(9, 30)
SQUEEZE_MIN_VOLUME_RATIO = 1.5
OVERSOLD_MAX_VOLUME_RATIO = 0.8
NEAR_LIMIT_UP_PCT = 9.0  # TW daily limit is 10%; above this the stock barely trades normally
MARKET_FALLING_PCT = -1.0  # median change across the snapshot
CLOSE_SLOT = "close"  # sorts after every "HH:MM" slot
CLOSE_VOLUME_MULTIPLE = 1.5  # the day's volume against the 20-session average
# Breakouts are what a close confirms; the bounce and divergence setups have no close check.
CLOSE_CHECKED: frozenset[Strategy] = frozenset(
    {Strategy.VOLATILITY_SQUEEZE, Strategy.CHIP_ACCUMULATION, Strategy.MOMENTUM_FLAG}
)
# Where each breakout strategy's level lives on the watchlist row, and the condition keys.
_BREAKOUT_LEVEL: dict[Strategy, tuple[str, str, str]] = {
    Strategy.VOLATILITY_SQUEEZE: ("upper_band", "price_above_upper_band", "close_above_upper_band"),
    Strategy.CHIP_ACCUMULATION: (
        "range_high_20d",
        "price_above_range_high",
        "close_above_range_high",
    ),
    Strategy.MOMENTUM_FLAG: ("flag_high", "price_above_flag_high", "close_above_flag_high"),
}


class Condition(BaseModel):
    """One checked fact. `key` names it for display (i18n key on the web side)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    kind: Literal["trigger", "guard"]
    met: bool
    observed: float | None = None
    required: float | None = None


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    strategy: Strategy
    slot: str  # "HH:MM", Taipei
    price: float
    change_rate: float | None
    volume_ratio: float | None
    conditions: tuple[Condition, ...]
    all_met: bool
    confirmed: bool
    alertable: bool

    def to_row(self, run_date: str) -> dict[str, object]:
        """A flat Parquet row; the conditions travel as one JSON column."""
        row = self.model_dump(mode="json", exclude={"conditions"})
        row["run_date"] = run_date
        row["conditions"] = json.dumps(
            [c.model_dump(mode="json") for c in self.conditions], ensure_ascii=False
        )
        return row


def slot_of(moment: time_) -> time_:
    """The 10-minute slot a wall-clock time falls in (09:47 -> 09:40)."""
    return time_(moment.hour, moment.minute - moment.minute % SLOT_MINUTES)


def market_change(snapshot: Iterable[Mapping[str, object]]) -> float | None:
    """The median change rate across ordinary stocks (4-digit codes) in the snapshot: a market
    filter that does not need a separate index request."""
    rates = [
        rate
        for row in snapshot
        if isinstance(symbol := row.get("symbol"), str)
        and len(symbol) == 4  # noqa: PLR2004 - TW ordinary-share codes are 4 digits
        and symbol.isdigit()
        and (rate := as_float(row.get("change_rate"))) is not None
    ]
    return statistics.median(rates) if rates else None


def _trigger(key: str, met: bool, observed: float | None, required: float | None) -> Condition:
    return Condition(key=key, kind="trigger", met=met, observed=observed, required=required)


def _guard(key: str, met: bool, observed: float | None, required: float | None) -> Condition:
    return Condition(key=key, kind="guard", met=met, observed=observed, required=required)


def _triggers(
    strategy: Strategy, watch: Mapping[str, object], price: float, quote: Mapping[str, object]
) -> list[Condition]:
    volume_ratio = as_float(quote.get("volume_ratio"))
    if strategy is Strategy.VOLATILITY_SQUEEZE:
        upper = as_float(watch.get("upper_band"))
        base = as_float(watch.get("close_5d_ago"))
        return [
            _trigger("price_above_upper_band", upper is not None and price > upper, price, upper),
            _trigger(
                "volume_ratio_at_least",
                volume_ratio is not None and volume_ratio >= SQUEEZE_MIN_VOLUME_RATIO,
                volume_ratio,
                SQUEEZE_MIN_VOLUME_RATIO,
            ),
            _trigger("momentum_5d_positive", base is not None and price > base, price, base),
        ]
    if strategy in (Strategy.CHIP_ACCUMULATION, Strategy.MOMENTUM_FLAG):
        level_key, condition_key, _ = _BREAKOUT_LEVEL[strategy]
        high = as_float(watch.get(level_key))
        return [
            _trigger(condition_key, high is not None and price > high, price, high),
            _trigger(
                "volume_ratio_at_least",
                volume_ratio is not None and volume_ratio >= SQUEEZE_MIN_VOLUME_RATIO,
                volume_ratio,
                SQUEEZE_MIN_VOLUME_RATIO,
            ),
        ]
    if strategy is Strategy.OVERSOLD_BOUNCE:
        open_ = as_float(quote.get("open"))
        k = as_float(watch.get("k"))
        return [
            _trigger("red_candle", open_ is not None and price > open_, price, open_),
            _trigger(
                "volume_ratio_at_most",
                volume_ratio is not None and volume_ratio <= OVERSOLD_MAX_VOLUME_RATIO,
                volume_ratio,
                OVERSOLD_MAX_VOLUME_RATIO,
            ),
            _trigger("kd_oversold", k is not None and k < KD_OVERSOLD, k, KD_OVERSOLD),
        ]
    low = as_float(watch.get("low_20d"))
    rsi_now = as_float(watch.get("rsi14"))
    rsi_low = as_float(watch.get("rsi_at_low"))
    ad = as_float(watch.get("ad_value"))
    ad_low = as_float(watch.get("ad_low_20d"))
    return [
        _trigger("new_20d_low", low is not None and price < low, price, low),
        _trigger(
            "rsi_higher_low",
            rsi_now is not None and rsi_low is not None and rsi_now > rsi_low,
            rsi_now,
            rsi_low,
        ),
        _trigger(
            "ad_higher_low", ad is not None and ad_low is not None and ad > ad_low, ad, ad_low
        ),
    ]


def _guards(
    strategy: Strategy,
    price: float,
    quote: Mapping[str, object],
    slot: time_,
    market: float | None,
) -> list[Condition]:
    guards = [
        _guard("after_opening_window", slot >= ALERT_FROM, None, None),
        _guard(
            "market_not_falling",
            market is None or market > MARKET_FALLING_PCT,
            market,
            MARKET_FALLING_PCT,
        ),
    ]
    if strategy is Strategy.AD_DIVERGENCE:
        return guards  # a new low is below the day's average by nature
    average = as_float(quote.get("average_price"))
    guards.append(
        _guard("above_average_price", average is None or price >= average, price, average)
    )
    if strategy in _BREAKOUT_LEVEL:
        change = as_float(quote.get("change_rate"))
        guards.append(
            _guard(
                "not_near_limit_up",
                change is None or change < NEAR_LIMIT_UP_PCT,
                change,
                NEAR_LIMIT_UP_PCT,
            )
        )
    return guards


def evaluate_slot(
    watchlist: Sequence[Mapping[str, object]],
    snapshot: Sequence[Mapping[str, object]],
    *,
    slot: time_,
    previously_met: frozenset[tuple[str, str]],
    market_pct: float | Literal["median"] | None = "median",
) -> list[Hit]:
    """One hit per watchlist row whose stock is in the snapshot with a price.

    The market filter is the median change of the snapshot ("median", right when the snapshot
    covers the whole market). A snapshot of just the watchlist is no market, so that caller passes
    the index change as `market_pct` instead (`None` leaves the filter open).

    `previously_met` holds the `(symbol, strategy)` pairs that were `all_met` on the previous
    slot today; a pair met twice in a row is `confirmed`."""
    quotes = {row["symbol"]: row for row in snapshot if isinstance(row.get("symbol"), str)}
    market = market_change(snapshot) if market_pct == "median" else market_pct
    hits: list[Hit] = []
    for watch in watchlist:
        symbol = watch.get("symbol")
        strategy_value = watch.get("strategy")
        if not isinstance(symbol, str) or not isinstance(strategy_value, str):
            continue
        quote = quotes.get(symbol)
        price = as_float(quote.get("close")) if quote is not None else None
        if quote is None or price is None or price <= 0:
            continue
        strategy = Strategy(strategy_value)
        triggers = _triggers(strategy, watch, price, quote)
        guards = _guards(strategy, price, quote, slot, market)
        all_met = all(c.met for c in triggers)
        confirmed = all_met and (symbol, strategy.value) in previously_met
        hits.append(
            Hit(
                symbol=symbol,
                strategy=strategy,
                slot=slot.strftime("%H:%M"),
                price=price,
                change_rate=as_float(quote.get("change_rate")),
                volume_ratio=as_float(quote.get("volume_ratio")),
                conditions=(*triggers, *guards),
                all_met=all_met,
                confirmed=confirmed,
                alertable=confirmed and all(c.met for c in guards),
            )
        )
    return hits


def evaluate_close(
    watchlist: Sequence[Mapping[str, object]], eod: Sequence[Mapping[str, object]]
) -> list[Hit]:
    """The close confirmation for the breakout strategies: today's close above the level and the
    day's volume at least `CLOSE_VOLUME_MULTIPLE` times the 20-session average. A closing price is
    the confirmation swing traders use, so a met close is alertable on its own."""
    bars = {row["symbol"]: row for row in eod if isinstance(row.get("symbol"), str)}
    hits: list[Hit] = []
    for watch in watchlist:
        symbol, strategy_value = watch.get("symbol"), watch.get("strategy")
        if not isinstance(symbol, str) or strategy_value not in {s.value for s in CLOSE_CHECKED}:
            continue
        bar = bars.get(symbol)
        close = as_float(bar.get("close")) if bar is not None else None
        if bar is None or close is None or close <= 0:
            continue
        strategy = Strategy(str(strategy_value))
        level_key, _, condition_key = _BREAKOUT_LEVEL[strategy]
        level = as_float(watch.get(level_key))
        volume = as_float(bar.get("volume"))
        average = as_float(watch.get("avg_volume_20d"))
        multiple = volume / average if volume is not None and average else None
        triggers = (
            _trigger(condition_key, level is not None and close > level, close, level),
            _trigger(
                "close_volume_multiple",
                multiple is not None and multiple >= CLOSE_VOLUME_MULTIPLE,
                multiple,
                CLOSE_VOLUME_MULTIPLE,
            ),
        )
        met = all(c.met for c in triggers)
        hits.append(
            Hit(
                symbol=symbol,
                strategy=strategy,
                slot=CLOSE_SLOT,
                price=close,
                change_rate=None,
                volume_ratio=multiple,
                conditions=triggers,
                all_met=met,
                confirmed=met,
                alertable=met,
            )
        )
    return hits


__all__ = [
    "ALERT_FROM",
    "CLOSE_SLOT",
    "SLOT_MINUTES",
    "Condition",
    "Hit",
    "evaluate_close",
    "evaluate_slot",
    "market_change",
    "slot_of",
]
