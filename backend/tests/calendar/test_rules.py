"""Layer 1 rule engine tests."""

from __future__ import annotations

from calendar.models import EventState, EventType
from calendar.rules import (
    financial_statement_deadline,
    futures_settlement_dates,
    generate_agm_derived_events,
    generate_derivatives_settlement_events,
    generate_financial_statement_deadline_event,
    generate_monthly_revenue_deadline_event,
    margin_short_covering_deadline,
    monthly_revenue_deadline,
    share_transfer_suspension_start,
    weekly_options_settlement_dates,
)
from calendar.trading_days import TradingDayCalendar
from datetime import date

from schemas.enums import Market


def test_futures_settlement_is_third_wednesday() -> None:
    settlement = futures_settlement_dates(2024, 1)

    assert settlement == date(2024, 1, 17)
    assert settlement.weekday() == 2  # Wednesday


def test_weekly_options_settlement_is_every_wednesday() -> None:
    wednesdays = weekly_options_settlement_dates(2024, 1)

    assert wednesdays == (
        date(2024, 1, 3),
        date(2024, 1, 10),
        date(2024, 1, 17),
        date(2024, 1, 24),
        date(2024, 1, 31),
    )


def test_generate_derivatives_settlement_events_are_whole_market_markers() -> None:
    events = generate_derivatives_settlement_events(Market.TW, 2024, 1)

    assert all(event.stock_id is None for event in events)
    assert any(event.type is EventType.FUTURES_SETTLEMENT for event in events)
    assert all(event.state is EventState.SCHEDULED for event in events)


def test_financial_statement_deadlines() -> None:
    assert financial_statement_deadline(2024, 1) == date(2024, 5, 15)
    assert financial_statement_deadline(2024, 2) == date(2024, 8, 14)
    assert financial_statement_deadline(2024, 2, is_finance_or_insurance=True) == date(2024, 8, 31)
    assert financial_statement_deadline(2024, 3) == date(2024, 11, 14)
    assert financial_statement_deadline(2024, 4) == date(2025, 3, 31)


def test_financial_statement_deadline_event_is_expected_window_not_scheduled() -> None:
    """The 'product correction': no fake scheduled date — a window marker only."""
    event = generate_financial_statement_deadline_event(Market.TW, "2330", 2024, 1)

    assert event.state is EventState.EXPECTED
    assert event.event_date is None
    assert event.window_start == event.window_end == date(2024, 5, 15)


def test_monthly_revenue_deadline_rolls_into_next_year() -> None:
    assert monthly_revenue_deadline(2024, 12) == date(2025, 1, 10)
    assert monthly_revenue_deadline(2024, 3) == date(2024, 4, 10)


def test_monthly_revenue_deadline_event_is_expected_window() -> None:
    event = generate_monthly_revenue_deadline_event(Market.TW, "2330", 2024, 3)

    assert event.state is EventState.EXPECTED
    assert event.window_start == event.window_end == date(2024, 4, 10)


def test_share_transfer_suspension_start_is_60_calendar_days_before_agm() -> None:
    agm = date(2024, 6, 20)

    assert share_transfer_suspension_start(agm) == date(2024, 4, 21)


def test_margin_short_covering_deadline_is_6_business_days_before_suspension() -> None:
    agm = date(2024, 6, 20)
    calendar = TradingDayCalendar()

    deadline = margin_short_covering_deadline(agm, calendar)

    assert deadline < share_transfer_suspension_start(agm)
    assert deadline.weekday() < 5


def test_generate_agm_derived_events_produce_scheduled_dates() -> None:
    agm = date(2024, 6, 20)
    calendar = TradingDayCalendar()

    events = generate_agm_derived_events(Market.TW, "2330", agm, calendar)

    assert len(events) == 2
    assert all(event.state is EventState.SCHEDULED for event in events)
    assert all(event.stock_id == "2330" for event in events)
    types = {event.type for event in events}
    assert types == {
        EventType.SHARE_TRANSFER_SUSPENSION_START,
        EventType.MARGIN_SHORT_COVERING_DEADLINE,
    }
