"""Layer 1 — the rule engine (`calendar/rules.py`).

Deterministic, holiday-aware markers generated with **zero feeds** — pure business-day math
off a `TradingDayCalendar` (`calendar/trading_days.py`). No I/O, no router call: this module
is a pure function library over dates.

Implements the following rules:
- 期貨結算日 (TX) = 3rd Wednesday of the month; 選擇權週結算 (TXO) = every Wednesday.
- 財報 statutory deadlines: Q1 5/15, Q2 8/14 (金融/保險業 8/31), Q3 11/14, 年報 3/31.
- 月營收 deadline = 10th of the following month (a **window** marker, state `expected`).
- 停止過戶起始日 = AGM - 60 days; 融券強制回補最後日 = 停止過戶起始日 - 6 營業日 (derived from an
  already-known AGM date — this module does not invent AGM dates, those are Layer 2 feed data).

Per the spec's "product correction": 財報/月營收 have **no per-company pre-scheduled date** —
only statutory deadlines, so they are rendered as `state=EventState.EXPECTED` deadline
markers, never as a false "scheduled" date.
"""

from __future__ import annotations

from calendar.models import CalendarEvent, EventState, EventType
from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from datetime import timedelta

from schemas.enums import Market

_WEDNESDAY = 2  # date.weekday(): Mon=0 ... Sun=6
_Q2_FINANCE_INDUSTRY_DEADLINE_MONTH = 8
_Q2_FINANCE_INDUSTRY_DEADLINE_DAY = 31
_DECEMBER = 12

_Q1 = 1
_Q2 = 2
_Q3 = 3
_Q4_ANNUAL = 4  # 年報


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date_:
    """The `n`-th (1-indexed) occurrence of `weekday` in `year-month`."""
    first_of_month = date_(year, month, 1)
    offset = (weekday - first_of_month.weekday()) % 7
    first_occurrence = first_of_month + timedelta(days=offset)
    return first_occurrence + timedelta(weeks=n - 1)


def _days_in_month(year: int, month: int) -> int:
    """Number of days in `year-month`, without importing the shadowed stdlib `calendar`
    module (this package is itself named `calendar`, which shadows the stdlib module of the
    same name)."""
    next_month_first = date_(year + 1, 1, 1) if month == _DECEMBER else date_(year, month + 1, 1)
    return (next_month_first - date_(year, month, 1)).days


def futures_settlement_dates(year: int, month: int) -> date_:
    """期貨結算日 (TX) — the 3rd Wednesday of `month`."""
    return _nth_weekday_of_month(year, month, _WEDNESDAY, 3)


def weekly_options_settlement_dates(year: int, month: int) -> tuple[date_, ...]:
    """選擇權週結算 (TXO) — every Wednesday in `month`."""
    days_in_month = _days_in_month(year, month)
    return tuple(
        date_(year, month, day)
        for day in range(1, days_in_month + 1)
        if date_(year, month, day).weekday() == _WEDNESDAY
    )


def generate_derivatives_settlement_events(
    market: Market, year: int, month: int
) -> tuple[CalendarEvent, ...]:
    """Whole-market (no `stock_id`) derivatives settlement markers for one month."""
    events: list[CalendarEvent] = []
    settlement_day = futures_settlement_dates(year, month)
    events.append(
        CalendarEvent(
            id=f"futures-settlement-{market.value}-{year:04d}-{month:02d}",
            market=market,
            type=EventType.FUTURES_SETTLEMENT,
            title_zh="期貨結算日",
            title_en="Futures Settlement Day",
            event_date=settlement_day,
            state=EventState.SCHEDULED,
            source="rule_engine",
        )
    )
    for weekly_day in weekly_options_settlement_dates(year, month):
        events.append(
            CalendarEvent(
                id=f"options-weekly-settlement-{market.value}-{weekly_day.isoformat()}",
                market=market,
                type=EventType.OPTIONS_WEEKLY_SETTLEMENT,
                title_zh="選擇權週結算",
                title_en="Weekly Options Settlement",
                event_date=weekly_day,
                state=EventState.SCHEDULED,
                source="rule_engine",
            )
        )
    return tuple(events)


def financial_statement_deadline(
    fiscal_year: int, quarter: int, *, is_finance_or_insurance: bool = False
) -> date_:
    """財報 statutory deadline for `quarter` (1-4; 4 = 年報) of `fiscal_year`.

    Q2's 8/14 deadline is extended to 8/31 for 金融/保險業 (industry-aware split).
    """
    if quarter == _Q1:
        return date_(fiscal_year, 5, 15)
    if quarter == _Q2:
        if is_finance_or_insurance:
            return date_(
                fiscal_year,
                _Q2_FINANCE_INDUSTRY_DEADLINE_MONTH,
                _Q2_FINANCE_INDUSTRY_DEADLINE_DAY,
            )
        return date_(fiscal_year, 8, 14)
    if quarter == _Q3:
        return date_(fiscal_year, 11, 14)
    if quarter == _Q4_ANNUAL:  # 年報 — due the following spring
        return date_(fiscal_year + 1, 3, 31)
    raise ValueError(f"quarter must be 1-4, got {quarter}")


def generate_financial_statement_deadline_event(
    market: Market,
    stock_id: str,
    fiscal_year: int,
    quarter: int,
    *,
    is_finance_or_insurance: bool = False,
) -> CalendarEvent:
    """財報 deadline **marker** — `state=EXPECTED`, never a fake "scheduled" per-company date,
    since there is no per-company pre-scheduled date, only a statutory deadline. Rendered as
    a single-day window `[deadline, deadline]` so the shared `window_start/window_end`
    contract applies uniformly."""
    deadline = financial_statement_deadline(
        fiscal_year, quarter, is_finance_or_insurance=is_finance_or_insurance
    )
    label = "年報" if quarter == _Q4_ANNUAL else f"Q{quarter} 財報"
    return CalendarEvent(
        id=f"financial-statement-deadline-{market.value}-{stock_id}-{fiscal_year}-Q{quarter}",
        market=market,
        stock_id=stock_id,
        type=EventType.FINANCIAL_STATEMENT_DEADLINE,
        title_zh=f"{label}公告截止日",
        title_en=f"{label} Filing Deadline",
        window_start=deadline,
        window_end=deadline,
        state=EventState.EXPECTED,
        source="rule_engine",
    )


def monthly_revenue_deadline(year: int, month: int) -> date_:
    """月營收 deadline = the 10th of the month **following** `year-month`."""
    if month == _DECEMBER:
        return date_(year + 1, 1, 10)
    return date_(year, month + 1, 10)


def generate_monthly_revenue_deadline_event(
    market: Market, stock_id: str, year: int, month: int
) -> CalendarEvent:
    """月營收 deadline **marker** — a window, not a scheduled date."""
    deadline = monthly_revenue_deadline(year, month)
    return CalendarEvent(
        id=f"monthly-revenue-deadline-{market.value}-{stock_id}-{year}-{month:02d}",
        market=market,
        stock_id=stock_id,
        type=EventType.MONTHLY_REVENUE_DEADLINE,
        title_zh=f"{year}年{month}月營收公告截止日",
        title_en=f"{year}-{month:02d} Monthly Revenue Filing Deadline",
        window_start=deadline,
        window_end=deadline,
        state=EventState.EXPECTED,
        source="rule_engine",
    )


def share_transfer_suspension_start(agm_date: date_) -> date_:
    """停止過戶起始日 = AGM - 60 calendar days."""
    return agm_date - timedelta(days=60)


def margin_short_covering_deadline(agm_date: date_, trading_calendar: TradingDayCalendar) -> date_:
    """融券強制回補最後日 = 停止過戶起始日 - 6 **營業日** (business days, per
    `TradingDayCalendar`)."""
    suspension_start = share_transfer_suspension_start(agm_date)
    return trading_calendar.shift_business_days(suspension_start, -6)


def generate_agm_derived_events(
    market: Market, stock_id: str, agm_date: date_, trading_calendar: TradingDayCalendar
) -> tuple[CalendarEvent, ...]:
    """停止過戶起始日 + 融券強制回補最後日 — both derived deterministically from an already-known
    AGM date (a Layer 2 feed datum, not invented here)."""
    suspension_start = share_transfer_suspension_start(agm_date)
    covering_deadline = margin_short_covering_deadline(agm_date, trading_calendar)
    return (
        CalendarEvent(
            id=f"share-transfer-suspension-{market.value}-{stock_id}-{agm_date.isoformat()}",
            market=market,
            stock_id=stock_id,
            type=EventType.SHARE_TRANSFER_SUSPENSION_START,
            title_zh="停止過戶起始日",
            title_en="Share Transfer Suspension Start",
            event_date=suspension_start,
            state=EventState.SCHEDULED,
            source="rule_engine",
        ),
        CalendarEvent(
            id=f"margin-short-covering-deadline-{market.value}-{stock_id}-{agm_date.isoformat()}",
            market=market,
            stock_id=stock_id,
            type=EventType.MARGIN_SHORT_COVERING_DEADLINE,
            title_zh="融券強制回補最後日",
            title_en="Margin Short Covering Deadline",
            event_date=covering_deadline,
            state=EventState.SCHEDULED,
            source="rule_engine",
        ),
    )


__all__ = [
    "financial_statement_deadline",
    "futures_settlement_dates",
    "generate_agm_derived_events",
    "generate_derivatives_settlement_events",
    "generate_financial_statement_deadline_event",
    "generate_monthly_revenue_deadline_event",
    "margin_short_covering_deadline",
    "monthly_revenue_deadline",
    "share_transfer_suspension_start",
    "weekly_options_settlement_dates",
]
