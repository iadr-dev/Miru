"""Calendar event model + state machine.

`CalendarEvent` is the shared shape produced by both the rule engine (`calendar/rules.py`)
and the feed/scraper composition layer (`calendar/events.py`) — deliberately market-
independent so a stock, an ETF, or a whole-market marker (期貨結算日, 央行理監事會議) fit the
same contract.
"""

from __future__ import annotations

from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import DataClass, Market


class EventState(StrEnum):
    """Every event carries a state so rule-derived markers upgrade gracefully and
    delays/early-releases become their own signal."""

    SCHEDULED = "scheduled"  # exact future date published
    EXPECTED = "expected"  # only a statutory deadline/window known — a marker, not a date
    CONFIRMED = "confirmed"  # a feed confirmed the specific date
    RELEASED = "released"  # the event happened / data landed
    REVISED = "revised"  # date changed after announcement


class EventType(StrEnum):
    """Event categories spanning the rule engine and feed/scraper layers (extend, never
    repurpose a value)."""

    FUTURES_SETTLEMENT = "futures_settlement"  # 期貨結算日
    OPTIONS_WEEKLY_SETTLEMENT = "options_weekly_settlement"  # 選擇權週結算
    FINANCIAL_STATEMENT_DEADLINE = "financial_statement_deadline"  # 財報 statutory deadline
    MONTHLY_REVENUE_DEADLINE = "monthly_revenue_deadline"  # 月營收 deadline (a window)
    SHARE_TRANSFER_SUSPENSION_START = "share_transfer_suspension_start"  # 停止過戶起始日
    MARGIN_SHORT_COVERING_DEADLINE = "margin_short_covering_deadline"  # 融券強制回補最後日
    DIVIDEND_EX_RIGHTS = "dividend_ex_rights"  # 除權息交易日/基準日/發放日 (feed-sourced)
    AGM = "agm"  # 股東會
    INVESTOR_CONFERENCE = "investor_conference"  # 法說會
    INDEX_REBALANCE = "index_rebalance"  # TIP/MSCI/FTSE (gap-scraper, Layer 3 — not built yet)
    MACRO_RELEASE = "macro_release"  # DGBAS/央行/FOMC (gap-scraper, Layer 3 — not built yet)
    CB_PUT_OR_MATURITY = "cb_put_or_maturity"  # CB 賣回權/到期 (feed-sourced, FinMind)


class CalendarEvent(BaseModel):
    """`CalendarEvent{id, stock_id|market, type, title_zh/en, date|window, state, source,
    data_class}`. Immutable — a state transition produces a
    new event via `with_state`, never an in-place mutation (consistent with the
    immutability discipline applied to canonical data project-wide)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    market: Market
    stock_id: str | None = None  # None => a whole-market marker (e.g. 期貨結算日)
    type: EventType
    title_zh: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    event_date: date_ | None = None  # set when `state` is scheduled/confirmed/released/revised
    window_start: date_ | None = None  # set when only a deadline *window* is known (expected)
    window_end: date_ | None = None
    state: EventState
    source: str = Field(min_length=1)  # "rule_engine" | a feed source_id | a gap-scraper id
    data_class: DataClass = DataClass.PUBLIC  # calendar events are public throughout

    @model_validator(mode="after")
    def _date_or_window(self) -> CalendarEvent:
        if self.state is EventState.EXPECTED:
            if self.window_start is None or self.window_end is None:
                raise ValueError("EXPECTED events must carry a window_start/window_end")
        elif self.event_date is None:
            raise ValueError(f"{self.state.value} events must carry an event_date")
        return self

    def with_state(
        self,
        state: EventState,
        *,
        event_date: date_ | None = None,
        source: str | None = None,
    ) -> CalendarEvent:
        """Return a new `CalendarEvent` reflecting a state transition — never mutates `self`."""
        return self.model_copy(
            update={
                "state": state,
                "event_date": event_date if event_date is not None else self.event_date,
                "source": source if source is not None else self.source,
            }
        )


__all__ = ["CalendarEvent", "EventState", "EventType"]
