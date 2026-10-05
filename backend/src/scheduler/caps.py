"""Uniform fair-use caps for alerts and schedules (modules/14).

These are abuse/resource limits applied identically to every signed-in user — not a tier
feature. The numbers are proposals (docs/backend/modules/12-multi-user-tiers.md) and live in one
place so changing them is a one-line edit.
"""

from __future__ import annotations

MAX_ALERT_RULES = 50
MAX_SCHEDULES = 10

__all__ = ["MAX_ALERT_RULES", "MAX_SCHEDULES"]
