"""Market-open detection — the intraday-session overlay on top of the day-level
`TradingDayCalendar`.

`TradingDayCalendar.is_trading_day` answers a date-level question (is this a trading date at
all). The live-quote poller needs a finer signal: *right now*, is the continuous-auction session
open, so a snapshot fetch is worth spending on. That is the day gate AND a wall-clock window.

There is no existing intraday-session signal anywhere in the codebase (the calendar module is
day-level, and no source exposes a session-open flag), so this is built here rather than
reused — the honest statement rather than pretending an existing helper covers it. It reuses
`TradingDayCalendar` for the day half so holiday/typhoon-closure handling stays in one place.

The default window is the TWSE continuous-auction session (09:00-13:30 Asia/Taipei). The
pre-open call-auction and the 13:30 closing call are intentionally treated as closed here: the
delayed snapshot source has nothing meaningfully ticking to fan out before 09:00, and the point
of the poller's market-open gate is to stop touching a symbol once the session that produces new
prints is over. Times are configurable so a non-TW market can supply its own window later.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as time_
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_TW_SESSION_OPEN = time_(9, 0)
_TW_SESSION_CLOSE = time_(13, 30)


@dataclass(frozen=True)
class MarketHours:
    """Is a market's continuous-auction session open at a given instant.

    Composes the day-level `TradingDayCalendar` (holidays + closure overlay) with an intraday
    session window in the market's own timezone. `is_open(now)` accepts any timezone-aware
    instant and converts it to the market's local wall clock itself, so callers pass
    `datetime.now(UTC)` and never do the conversion.
    """

    calendar: TradingDayCalendar = field(default_factory=TradingDayCalendar)
    tz: ZoneInfo = _TAIPEI
    session_open: time_ = _TW_SESSION_OPEN
    session_close: time_ = _TW_SESSION_CLOSE

    def is_open(self, now: datetime) -> bool:
        """True if `now` falls on a trading day and within the session window (local time).

        A naive `now` is assumed to already be in the market's timezone; an aware one is
        converted. Never raises — a bad/edge instant simply reads as closed.
        """
        local = now.astimezone(self.tz) if now.tzinfo is not None else now.replace(tzinfo=self.tz)
        if not self.calendar.is_trading_day(local.date()):
            return False
        return self.session_open <= local.timetz().replace(tzinfo=None) <= self.session_close


__all__ = ["MarketHours"]
