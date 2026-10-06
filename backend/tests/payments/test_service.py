"""The webhook -> entitlement lifecycle: created/activated/canceled tier transitions, idempotent
redelivery, out-of-order rejection, scheduled-cancel NOT downgrading, and reconcile-on-read
self-healing a missing local row. `users.tier` is written only through this path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from accounts.store import SqlAlchemyUserStore
from config.settings import Settings
from payments.service import BillingService, effective_byok_active
from payments.store import SqlAlchemySubscriptionStore
from payments.webhooks import PaddleEvent
from schemas.enums import Tier
from tests.payments.conftest import (
    PRICE_PREMIUM_MONTHLY,
    PRICE_PRO_MONTHLY,
    make_user,
)

pytestmark = pytest.mark.governance


def _event(  # noqa: PLR0913 — one kwarg per webhook field the tests vary
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    user_id: str,
    subscription_id: str = "sub_1",
    price_id: str = PRICE_PREMIUM_MONTHLY,
    status: str = "active",
    scheduled_change: dict[str, Any] | None = None,
) -> PaddleEvent:
    data: dict[str, Any] = {
        "id": subscription_id,
        "customer_id": "ctm_1",
        "status": status,
        "items": [{"price": {"id": price_id}}],
        "current_billing_period": {
            "starts_at": "2024-05-01T00:00:00Z",
            "ends_at": "2024-06-01T00:00:00Z",
        },
        "scheduled_change": scheduled_change,
        "custom_data": {"user_id": user_id},
    }
    return PaddleEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        notification_id="ntf_1",
        data=data,
    )


def _service(session, settings: Settings) -> BillingService:
    return BillingService(
        store=SqlAlchemySubscriptionStore(session),
        user_store=SqlAlchemyUserStore(session),
        settings=settings,
        client=None,
    )


async def test_created_then_canceled_lifecycle(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="evt_1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM

    await svc.handle_webhook_event(
        _event(
            event_id="evt_2",
            event_type="subscription.canceled",
            occurred_at=t0 + timedelta(days=30),
            user_id=user_id,
            status="canceled",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.FREE


async def test_updated_remaps_tier(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    # Plan change Premium -> Pro.
    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.updated",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            price_id=PRICE_PRO_MONTHLY,
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PRO


async def test_redelivered_event_is_idempotent(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    store = SqlAlchemySubscriptionStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)
    evt = _event(
        event_id="evt_same", event_type="subscription.created", occurred_at=t0, user_id=user_id
    )

    await svc.handle_webhook_event(evt)
    first = await store.get_for_user(user_id=user_id)
    assert first is not None
    # Redeliver the SAME event id — must be a no-op (no second row, no state churn).
    await svc.handle_webhook_event(evt)
    again = await store.get_for_user(user_id=user_id)
    assert again is not None
    assert again.updated_from_event_at == first.updated_from_event_at


async def test_out_of_order_event_does_not_regress_tier(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    # Newer cancel arrives first (downgrade to free).
    await svc.handle_webhook_event(
        _event(
            event_id="evt_cancel",
            event_type="subscription.canceled",
            occurred_at=t0 + timedelta(days=10),
            user_id=user_id,
            status="canceled",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.FREE

    # An OLDER 'updated' (still-active) event is delivered late — must NOT resurrect the sub.
    await svc.handle_webhook_event(
        _event(
            event_id="evt_stale_update",
            event_type="subscription.updated",
            occurred_at=t0,
            user_id=user_id,
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.FREE


async def test_scheduled_cancel_does_not_downgrade(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    store = SqlAlchemySubscriptionStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    # An 'updated' carrying a scheduled cancel — display the date, but stay active/Premium.
    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.updated",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            status="active",
            scheduled_change={"action": "cancel", "effective_at": "2024-06-01T00:00:00Z"},
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM
    record = await store.get_for_user(user_id=user_id)
    assert record is not None
    assert record.status == "active"
    assert record.scheduled_cancel_at is not None


async def test_past_due_keeps_entitlement(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    store = SqlAlchemySubscriptionStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.past_due",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            status="past_due",
        )
    )
    # Grace state: tier unchanged during dunning, but status reflects past_due.
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM
    record = await store.get_for_user(user_id=user_id)
    assert record is not None
    assert record.status == "past_due"


async def test_unknown_price_is_ignored(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)
    await svc.handle_webhook_event(
        _event(
            event_id="e1",
            event_type="subscription.created",
            occurred_at=t0,
            user_id=user_id,
            price_id="pri_unrecognized",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.FREE


def test_effective_byok_requires_stored_key() -> None:
    # A BYOK subscription with no stored key falls back to non-BYOK entitlement (never silently
    # billed the standard rate).
    assert effective_byok_active(subscription_byok=True, has_llm_key=False) is False
    assert effective_byok_active(subscription_byok=True, has_llm_key=True) is True
    assert effective_byok_active(subscription_byok=False, has_llm_key=True) is False


async def test_paused_holds_the_paid_tier_back_and_resumed_restores_it(
    db_session, settings: Settings
) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    store = SqlAlchemySubscriptionStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM

    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.paused",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            status="paused",
        )
    )
    # Billing is stopped, so there is nothing to entitle - but the row survives for the resume.
    assert (await users.get(user_id=user_id)).tier is Tier.FREE
    record = await store.get_for_user(user_id=user_id)
    assert record is not None
    assert record.status == "paused"
    assert record.price_id == PRICE_PREMIUM_MONTHLY

    await svc.handle_webhook_event(
        _event(
            event_id="e3",
            event_type="subscription.resumed",
            occurred_at=t0 + timedelta(hours=2),
            user_id=user_id,
            status="active",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM
    record = await store.get_for_user(user_id=user_id)
    assert record is not None
    assert record.status == "active"


async def test_an_update_that_reports_paused_also_holds_the_tier_back(
    db_session, settings: Settings
) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    # Paddle also reports a pause as a plain `subscription.updated` carrying status=paused.
    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.updated",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            status="paused",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.FREE


async def test_a_late_paused_event_cannot_undo_a_newer_resume(
    db_session, settings: Settings
) -> None:
    user_id = await make_user(db_session)
    svc = _service(db_session, settings)
    users = SqlAlchemyUserStore(db_session)
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    await svc.handle_webhook_event(
        _event(event_id="e1", event_type="subscription.created", occurred_at=t0, user_id=user_id)
    )
    await svc.handle_webhook_event(
        _event(
            event_id="e3",
            event_type="subscription.resumed",
            occurred_at=t0 + timedelta(hours=2),
            user_id=user_id,
        )
    )
    # The pause arrives after the resume it precedes (out-of-order delivery): ignored.
    await svc.handle_webhook_event(
        _event(
            event_id="e2",
            event_type="subscription.paused",
            occurred_at=t0 + timedelta(hours=1),
            user_id=user_id,
            status="paused",
        )
    )
    assert (await users.get(user_id=user_id)).tier is Tier.PREMIUM
