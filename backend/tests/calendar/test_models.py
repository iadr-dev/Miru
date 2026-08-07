"""CalendarEvent model + state-transition tests."""

from __future__ import annotations

from calendar.models import CalendarEvent, EventState, EventType
from datetime import date

import pytest
from pydantic import ValidationError

from schemas.enums import Market


def test_expected_state_requires_window() -> None:
    with pytest.raises(ValidationError):
        CalendarEvent(
            id="x",
            market=Market.TW,
            type=EventType.FINANCIAL_STATEMENT_DEADLINE,
            title_zh="財報",
            title_en="Filing",
            state=EventState.EXPECTED,
        )


def test_scheduled_state_requires_event_date() -> None:
    with pytest.raises(ValidationError):
        CalendarEvent(
            id="x",
            market=Market.TW,
            type=EventType.AGM,
            title_zh="股東會",
            title_en="AGM",
            state=EventState.SCHEDULED,
        )


def test_with_state_returns_new_instance_never_mutates() -> None:
    event = CalendarEvent(
        id="x",
        market=Market.TW,
        type=EventType.AGM,
        title_zh="股東會",
        title_en="AGM",
        event_date=date(2024, 6, 20),
        state=EventState.SCHEDULED,
        source="mops",
    )

    confirmed = event.with_state(EventState.CONFIRMED, source="mops_confirmed")

    assert confirmed is not event
    assert confirmed.state is EventState.CONFIRMED
    assert confirmed.source == "mops_confirmed"
    assert event.state is EventState.SCHEDULED  # original untouched
    assert event.source == "mops"


def test_calendar_event_is_frozen() -> None:
    event = CalendarEvent(
        id="x",
        market=Market.TW,
        type=EventType.AGM,
        title_zh="股東會",
        title_en="AGM",
        event_date=date(2024, 6, 20),
        state=EventState.SCHEDULED,
        source="mops",
    )

    with pytest.raises(ValidationError):
        event.state = EventState.CONFIRMED  # type: ignore[misc]
