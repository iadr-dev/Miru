"""Scheduler & alerts HTTP surface: schedules, alert rules, notifications, alert preferences,
and native-push device registration.

Every route is behind `require_user` and user-scoped — the store filters on `user_id`, so a
caller can never read or mutate another user's schedules. Alerts and schedules are open to every
signed-in user (modules/14) under uniform fair-use caps (`scheduler.caps`); what a tier gates is the
*data* a notification carries, not the feature. A creation request is validated against the
scheduling bounds (coarse cadence, real skill) and given a concrete `next_run` before it is stored
— without one the worker's `due_jobs` scan would never see it.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.routers.scheduler import (
    alerts,
    channels,
    email,
    notifications,
    schedules,
    strategy_watch,
)
from api.routers.scheduler.channels import get_line_transport

router = APIRouter(tags=["scheduler"])
for _part in (schedules, alerts, notifications, channels, email, strategy_watch):
    router.include_router(_part.router)

__all__ = ["get_line_transport", "router"]
