"""Creation-time validation of the scheduling bounds a job must satisfy.

These checks are the enforcement, in code, of the coarse-cadence and read-only bounds the
scheduling amendment is permitted under: a trigger must be a coarse market event or a
weekday-cron (never a sub-daily / tick cadence), and the referenced skill must be a real,
governed skill. A job that violates a bound is refused at `POST /schedules`, not silently
accepted and then skipped forever.
"""

from __future__ import annotations

import re

from scheduler.models import MarketEvent, ScheduleAction, ScheduleTrigger, TriggerType
from scheduler.triggers import UnresolvableTriggerError, validate_cron
from skills.registry import SkillRegistry, UnknownSkillError

# A weekday-cron field set: 5 fields, and the minute/hour fields must be concrete (a `*` in the
# minute field is a per-minute cadence — rejected as too frequent for the coarse bound).
_CRON_FIELDS = 5
_STAR_STEP = re.compile(r"^\*(/\d+)?$")


class InvalidTriggerError(ValueError):
    pass


class UnknownSkillReferenceError(ValueError):
    pass


def validate_trigger(trigger: ScheduleTrigger) -> None:
    if trigger.type is TriggerType.MARKET_EVENT:
        try:
            MarketEvent(trigger.spec)
        except ValueError as exc:
            raise InvalidTriggerError(
                f"market_event spec must be one of {[e.value for e in MarketEvent]}"
            ) from exc
        return
    _validate_cron(trigger.spec)


def _validate_cron(spec: str) -> None:
    fields = spec.split()
    if len(fields) != _CRON_FIELDS:
        raise InvalidTriggerError("cron must have 5 fields (min hour dom mon dow)")
    minute, hour = fields[0], fields[1]
    if _STAR_STEP.match(minute) or _STAR_STEP.match(hour):
        # A wildcard minute or hour is a high-frequency cadence — forbidden by the coarse-cadence
        # bound. A scheduled job fires at a concrete post-close/pre-open-shaped clock time.
        raise InvalidTriggerError(
            "cron minute and hour must be concrete (no wildcard) — coarse cadence only"
        )
    try:
        validate_cron(spec)
    except UnresolvableTriggerError as exc:
        raise InvalidTriggerError(str(exc)) from exc


def validate_action(action: ScheduleAction, registry: SkillRegistry) -> None:
    try:
        registry.get(action.skill_ref)
    except UnknownSkillError as exc:
        raise UnknownSkillReferenceError(
            f"skill {action.skill_ref!r} is not a registered skill"
        ) from exc


__all__ = [
    "InvalidTriggerError",
    "UnknownSkillReferenceError",
    "validate_action",
    "validate_trigger",
]
