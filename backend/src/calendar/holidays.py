"""Forward-looking market-closure calendar — the holidays a schedule must skip.

`TradingDayCalendar` built from ingested partition dates is ground truth for the *past* but says
nothing about a future date, which is exactly what a schedule's `next_run` needs. This module adds
a rule-based holiday overlay for the two supported markets: Taiwan public holidays (TWSE closes on
them) and the NYSE holiday calendar. Pure functions — no I/O, no clock.

Known limits (deliberate, documented): ad-hoc closures announced at short notice (typhoon days) are
not predictable and are not covered; the Taiwan list is the public-holiday calendar minus the
commemorative days that are not days off for the exchange.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import holidays as _holidays

from schemas.enums import Market

_WEEKEND_START = 5  # date.weekday(): Sat=5, Sun=6

# Public-holiday names (zh_TW) that are observed as commemorations but do NOT close the exchange.
_TW_NOT_A_CLOSURE = ("孔子誕辰", "教師節")
# 行憲紀念日 stopped being a day off from 2025.
_TW_CONSTITUTION_DAY = "行憲紀念日"
_TW_CONSTITUTION_LAST_HOLIDAY_YEAR = 2024


@lru_cache(maxsize=16)
def _tw_closures(year: int) -> frozenset[date]:
    names = _holidays.country_holidays("TW", years=year, language="zh_TW")
    closed: set[date] = set()
    for day, name in names.items():
        if any(marker in name for marker in _TW_NOT_A_CLOSURE):
            continue
        if _TW_CONSTITUTION_DAY in name and day.year > _TW_CONSTITUTION_LAST_HOLIDAY_YEAR:
            continue
        closed.add(day)
    return frozenset(closed)


@lru_cache(maxsize=16)
def _us_closures(year: int) -> frozenset[date]:
    return frozenset(_holidays.financial_holidays("XNYS", years=year))


def is_market_holiday(day: date, market: Market) -> bool:
    """True if `day` is a scheduled exchange closure for `market` (weekends excluded)."""
    closures = _tw_closures(day.year) if market is Market.TW else _us_closures(day.year)
    return day in closures


def is_market_open_day(day: date, market: Market) -> bool:
    """A weekday that is not a scheduled closure."""
    return day.weekday() < _WEEKEND_START and not is_market_holiday(day, market)


__all__ = ["is_market_holiday", "is_market_open_day"]
