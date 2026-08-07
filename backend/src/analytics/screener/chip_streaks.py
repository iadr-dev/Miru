"""Pure chip-flow streak predicates over an already-extracted daily net-buy series.

These take a plain `Sequence[float]` of per-trading-day net-buy figures (buy minus sell for one
investor category), newest-last, and answer a single yes/no. They are deliberately I/O-free: the
batch job that owns them reads the real `institutional_flow` partitions, groups by symbol and
investor category, orders the days by the trading calendar, and hands the resulting net series in.
Keeping the predicates pure means the streak/window semantics are unit-testable in isolation from
the store, and the two shapes below never get conflated.

`net_buy_streak` and `cumulative_net_buy` are genuinely different questions: a strict consecutive-
days streak ("外資連3買" — the last N days were each individually a net buy) versus a trailing-
window cumulative ("近5日買超" — the sum over the last N days is positive, tolerating an odd
down day inside the window). A symbol can satisfy one and not the other, so they are separate.
"""

from __future__ import annotations

from collections.abc import Sequence


def net_buy_streak(daily_net: Sequence[float], min_days: int) -> bool:
    """The last `min_days` trading days were EACH a net buy (every one strictly positive) — the
    strict consecutive-days semantics of "連N買". `False` if fewer than `min_days` days are
    available (an unproven streak is never asserted). `min_days` must be >= 1."""
    if min_days < 1:
        raise ValueError("min_days must be >= 1")
    if len(daily_net) < min_days:
        return False
    return all(value > 0 for value in daily_net[-min_days:])


def net_sell_streak(daily_net: Sequence[float], min_days: int) -> bool:
    """The last `min_days` trading days were EACH a net sell (every one strictly negative) — the
    "連N賣" mirror of `net_buy_streak`."""
    if min_days < 1:
        raise ValueError("min_days must be >= 1")
    if len(daily_net) < min_days:
        return False
    return all(value < 0 for value in daily_net[-min_days:])


def cumulative_net_buy(daily_net: Sequence[float], window: int) -> bool:
    """The SUM of net-buy over the trailing `window` trading days is positive — the looser
    "近N日買超" semantics, which tolerates an individual down day as long as the window nets to
    accumulation. `False` if fewer than `window` days are available (a partial window is not a
    proven `window`-day accumulation). `window` must be >= 1."""
    if window < 1:
        raise ValueError("window must be >= 1")
    if len(daily_net) < window:
        return False
    return sum(daily_net[-window:]) > 0


__all__ = ["cumulative_net_buy", "net_buy_streak", "net_sell_streak"]
