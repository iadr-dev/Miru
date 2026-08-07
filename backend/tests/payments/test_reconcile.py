"""Reconcile-on-read: `GET /billing/subscription` reads through to the live Paddle API and
upserts the local row, so a dropped/delayed webhook self-heals on the next read."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from accounts.store import SqlAlchemyUserStore
from config.settings import Settings
from payments.client import PaddleClient
from payments.service import BillingService
from payments.store import SqlAlchemySubscriptionStore, SubscriptionUpdate
from schemas.enums import Tier
from tests.payments.conftest import PRICE_PRO_MONTHLY, make_user


def _mock_client(handler: Any, settings: Settings) -> PaddleClient:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return PaddleClient(settings, client_factory=factory)


async def test_reconcile_self_heals_from_live_api(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemySubscriptionStore(db_session)
    # A stale local row that says Premium — simulate a missed 'updated' webhook to Pro.
    await store.upsert_from_event(
        SubscriptionUpdate(
            user_id=user_id,
            paddle_subscription_id="sub_1",
            paddle_customer_id="ctm_1",
            status="active",
            price_id=settings.paddle_price_premium_monthly or "",
            tier=Tier.PREMIUM,
            byok=False,
            current_period_start=None,
            current_period_end=None,
            scheduled_cancel_at=None,
            occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/subscriptions/sub_1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "sub_1",
                    "customer_id": "ctm_1",
                    "status": "active",
                    "items": [{"price": {"id": PRICE_PRO_MONTHLY}}],
                }
            },
        )

    svc = BillingService(
        store=store,
        user_store=SqlAlchemyUserStore(db_session),
        settings=settings,
        client=_mock_client(handler, settings),
    )
    record = await svc.reconcile_subscription(user_id=user_id)
    assert record is not None
    assert record.tier is Tier.PRO
    # The derived tier cache is healed too.
    assert (await SqlAlchemyUserStore(db_session).get(user_id=user_id)).tier is Tier.PRO


async def test_reconcile_without_local_row_returns_none(db_session, settings: Settings) -> None:
    user_id = await make_user(db_session)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover — never called
        return httpx.Response(200, json={"data": {}})

    svc = BillingService(
        store=SqlAlchemySubscriptionStore(db_session),
        user_store=SqlAlchemyUserStore(db_session),
        settings=settings,
        client=_mock_client(handler, settings),
    )
    assert await svc.reconcile_subscription(user_id=user_id) is None
