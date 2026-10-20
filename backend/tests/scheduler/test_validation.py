"""Creation-time trigger/skill validation — the coarse-cadence and real-skill bounds enforced
at `POST /schedules`."""

from __future__ import annotations

from pathlib import Path

import pytest

from scheduler.models import ScheduleAction, ScheduleTrigger, TriggerType
from scheduler.validation import (
    InvalidTriggerError,
    UnknownSkillReferenceError,
    validate_action,
    validate_trigger,
)
from skills.registry import SkillRegistry

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"


@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load([_BUILT_INS])
    return reg


def test_market_event_trigger_accepted() -> None:
    validate_trigger(ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"))


def test_unknown_market_event_rejected() -> None:
    trigger = ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="every_tick")
    with pytest.raises(InvalidTriggerError):
        validate_trigger(trigger)


def test_concrete_weekday_cron_accepted() -> None:
    validate_trigger(ScheduleTrigger(type=TriggerType.CRON, spec="40 17 * * 1-5"))


def test_wildcard_minute_cron_rejected_as_too_frequent() -> None:
    trigger = ScheduleTrigger(type=TriggerType.CRON, spec="* 17 * * 1-5")
    with pytest.raises(InvalidTriggerError):
        validate_trigger(trigger)


def test_malformed_cron_rejected() -> None:
    trigger = ScheduleTrigger(type=TriggerType.CRON, spec="17 40")
    with pytest.raises(InvalidTriggerError):
        validate_trigger(trigger)


def test_unknown_skill_rejected(registry: SkillRegistry) -> None:
    action = ScheduleAction(skill_ref="not-a-real-skill")
    with pytest.raises(UnknownSkillReferenceError):
        validate_action(action, registry)


def test_real_skill_accepted(registry: SkillRegistry) -> None:
    validate_action(ScheduleAction(skill_ref="daily-context-brief"), registry)
