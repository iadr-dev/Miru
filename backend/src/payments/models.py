"""Domain models for the payments module: the local subscription record and the parsed shape of
a Paddle subscription entity (as it arrives in a webhook `data` field or a `GET /subscriptions`
response).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from schemas.enums import Tier


class SubscriptionRecord(BaseModel):
    """The local `subscriptions` row as a typed value — the record `GET /billing/subscription`
    returns and the webhook writes. `tier`/`byok` are derived from the active price_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    paddle_subscription_id: str
    paddle_customer_id: str | None
    status: str
    price_id: str
    tier: Tier
    byok: bool
    current_period_start: datetime | None
    current_period_end: datetime | None
    scheduled_cancel_at: datetime | None
    updated_from_event_at: datetime
    created_at: datetime
    updated_at: datetime


class ScheduledChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    action: str  # cancel | pause | resume
    effective_at: datetime | None = None
    resume_at: datetime | None = None


class PaddleSubscriptionEntity(BaseModel):
    """The subset of a Paddle subscription entity this module reads. `extra='ignore'` because
    Paddle carries many more fields we don't consume. The first item's `price.id` is the plan we
    map to a tier; `scheduled_change.action == 'cancel'` is the "cancels on <date>, don't
    downgrade yet" signal."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    customer_id: str | None = None
    status: str
    items: list[dict[str, Any]] = []
    current_billing_period: dict[str, Any] | None = None
    scheduled_change: ScheduledChange | None = None
    custom_data: dict[str, Any] | None = None

    def first_price_id(self) -> str | None:
        for item in self.items:
            price: Any = item.get("price")
            if isinstance(price, dict):
                price_id = cast("dict[str, Any]", price).get("id")
                if isinstance(price_id, str):
                    return price_id
        return None

    def period_start(self) -> datetime | None:
        return _parse_dt((self.current_billing_period or {}).get("starts_at"))

    def period_end(self) -> datetime | None:
        return _parse_dt((self.current_billing_period or {}).get("ends_at"))

    def scheduled_cancel_at(self) -> datetime | None:
        if self.scheduled_change is not None and self.scheduled_change.action == "cancel":
            return self.scheduled_change.effective_at
        return None

    def user_id(self) -> str | None:
        value = (self.custom_data or {}).get("user_id")
        return value if isinstance(value, str) else None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "PaddleSubscriptionEntity",
    "ScheduledChange",
    "SubscriptionRecord",
]
