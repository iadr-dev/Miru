"""Characterization of the billing router's `_portal_url`: which management link a subscriber is
sent to, and when it falls back to the subscription's own links. Stub Paddle client, no network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pytest

from api.routers.billing import _portal_url
from payments.client import PaddleApiError, PaddleClient
from payments.models import SubscriptionRecord
from schemas.enums import Tier

NOW = datetime(2026, 10, 1, tzinfo=UTC)
Purpose = Literal["overview", "payment"]


def record(customer_id: str | None = "ctm_1") -> SubscriptionRecord:
    return SubscriptionRecord(
        id="s1",
        user_id="u1",
        paddle_subscription_id="sub_1",
        paddle_customer_id=customer_id,
        status="active",
        price_id="pri_1",
        tier=Tier.PREMIUM,
        byok=False,
        current_period_start=None,
        current_period_end=None,
        scheduled_cancel_at=None,
        updated_from_event_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class StubPaddle:
    def __init__(
        self,
        *,
        session: dict[str, Any] | Exception | None = None,
        subscription: dict[str, Any] | Exception | None = None,
    ) -> None:
        self.session = session if session is not None else {}
        self.subscription = subscription if subscription is not None else {}
        self.session_calls: list[tuple[str, list[str]]] = []
        self.subscription_calls: list[str] = []

    async def create_portal_session(
        self, customer_id: str, subscription_ids: list[str]
    ) -> dict[str, Any]:
        self.session_calls.append((customer_id, subscription_ids))
        if isinstance(self.session, Exception):
            raise self.session
        return self.session

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        self.subscription_calls.append(subscription_id)
        if isinstance(self.subscription, Exception):
            raise self.subscription
        return self.subscription


async def resolve(
    stub: StubPaddle, purpose: Purpose = "overview", rec: SubscriptionRecord | None = None
) -> str | None:
    return await _portal_url(stub, rec or record(), purpose)  # type: ignore[arg-type]


def test_stub_matches_the_client_surface() -> None:
    assert hasattr(PaddleClient, "create_portal_session")
    assert hasattr(PaddleClient, "get_subscription")


FALLBACK = {
    "management_urls": {"update_payment_method": "https://fb/card", "cancel": "https://fb/x"}
}


@pytest.mark.parametrize("purpose", ["overview", "payment"])
async def test_a_portal_session_is_asked_for_with_the_customer_and_subscription(
    purpose: Purpose,
) -> None:
    stub = StubPaddle(subscription=FALLBACK)
    await resolve(stub, purpose)

    assert stub.session_calls == [("ctm_1", ["sub_1"])]


async def test_overview_uses_the_general_overview_link_and_skips_the_subscription_read() -> None:
    stub = StubPaddle(
        session={
            "urls": {
                "general": {"overview": "https://p/overview"},
                "subscriptions": [{"update_subscription_payment_method": "https://p/card"}],
            }
        },
        subscription=FALLBACK,
    )

    assert await resolve(stub, "overview") == "https://p/overview"
    assert stub.subscription_calls == []


async def test_payment_uses_the_first_subscriptions_card_link_and_skips_the_subscription_read() -> (
    None
):
    stub = StubPaddle(
        session={
            "urls": {
                "general": {"overview": "https://p/overview"},
                "subscriptions": [
                    {"update_subscription_payment_method": "https://p/card"},
                    {"update_subscription_payment_method": "https://p/second"},
                ],
            }
        },
        subscription=FALLBACK,
    )

    assert await resolve(stub, "payment") == "https://p/card"
    assert stub.subscription_calls == []


@pytest.mark.parametrize(
    "session",
    [
        {},
        {"urls": None},
        {"urls": {}},
        {"urls": {"general": None}},
        {"urls": {"general": {}}},
        {"urls": {"general": {"overview": ""}}},
        {"urls": {"general": {"overview": None}}},
        # the card link is irrelevant to an overview request
        {"urls": {"subscriptions": [{"update_subscription_payment_method": "https://p/card"}]}},
    ],
)
async def test_overview_without_an_overview_link_falls_back_to_the_subscription(
    session: dict[str, Any],
) -> None:
    stub = StubPaddle(session=session, subscription=FALLBACK)

    assert await resolve(stub, "overview") == "https://fb/card"
    assert stub.subscription_calls == ["sub_1"]


@pytest.mark.parametrize(
    "session",
    [
        {},
        {"urls": None},
        {"urls": {}},
        {"urls": {"subscriptions": None}},
        {"urls": {"subscriptions": []}},
        {"urls": {"subscriptions": [{}]}},
        {"urls": {"subscriptions": [{"update_subscription_payment_method": ""}]}},
        {"urls": {"subscriptions": [{"update_subscription_payment_method": None}]}},
        # only the first subscription is looked at
        {
            "urls": {
                "subscriptions": [{}, {"update_subscription_payment_method": "https://p/second"}]
            }
        },
        # the overview link is irrelevant to a payment request
        {"urls": {"general": {"overview": "https://p/overview"}}},
    ],
)
async def test_payment_without_a_card_link_falls_back_to_the_subscription(
    session: dict[str, Any],
) -> None:
    stub = StubPaddle(session=session, subscription=FALLBACK)

    assert await resolve(stub, "payment") == "https://fb/card"
    assert stub.subscription_calls == ["sub_1"]


@pytest.mark.parametrize("purpose", ["overview", "payment"])
async def test_a_failed_portal_session_falls_back_to_the_subscription_links(
    purpose: Purpose,
) -> None:
    stub = StubPaddle(session=PaddleApiError("boom"), subscription=FALLBACK)

    assert await resolve(stub, purpose) == "https://fb/card"
    assert stub.subscription_calls == ["sub_1"]


@pytest.mark.parametrize("customer_id", [None, ""])
@pytest.mark.parametrize("purpose", ["overview", "payment"])
async def test_without_a_customer_no_portal_session_is_attempted(
    customer_id: str | None, purpose: Purpose
) -> None:
    stub = StubPaddle(
        session={"urls": {"general": {"overview": "https://p/overview"}}}, subscription=FALLBACK
    )

    assert await resolve(stub, purpose, record(customer_id)) == "https://fb/card"
    assert stub.session_calls == []


@pytest.mark.parametrize(
    ("subscription", "expected"),
    [
        (FALLBACK, "https://fb/card"),
        ({"management_urls": {"cancel": "https://fb/x"}}, "https://fb/x"),
        (
            {"management_urls": {"update_payment_method": "", "cancel": "https://fb/x"}},
            "https://fb/x",
        ),
        (
            {"management_urls": {"update_payment_method": None, "cancel": "https://fb/x"}},
            "https://fb/x",
        ),
        ({"management_urls": {"update_payment_method": "https://fb/card"}}, "https://fb/card"),
        ({"management_urls": {"update_payment_method": "", "cancel": ""}}, None),
        ({"management_urls": {}}, None),
        ({"management_urls": None}, None),
        ({}, None),
    ],
)
async def test_the_subscriptions_own_links_prefer_the_card_form_then_cancel(
    subscription: dict[str, Any], expected: str | None
) -> None:
    stub = StubPaddle(subscription=subscription)

    assert await resolve(stub, "overview") == expected
    assert await resolve(stub, "payment") == expected


async def test_link_values_are_returned_as_text() -> None:
    stub = StubPaddle(session={"urls": {"general": {"overview": 12345}}})

    assert await resolve(stub, "overview") == "12345"


async def test_a_failing_subscription_read_propagates_for_the_route_to_map() -> None:
    stub = StubPaddle(subscription=PaddleApiError("down"))

    with pytest.raises(PaddleApiError):
        await resolve(stub)


async def test_a_failing_subscription_read_propagates_without_a_customer_too() -> None:
    stub = StubPaddle(subscription=PaddleApiError("down"))

    rec = record(None)
    with pytest.raises(PaddleApiError):
        await resolve(stub, "payment", rec)


async def test_only_a_paddle_api_error_is_swallowed_from_the_portal_session() -> None:
    stub = StubPaddle(session=ValueError("not a provider error"), subscription=FALLBACK)

    with pytest.raises(ValueError, match="not a provider error"):
        await resolve(stub)
    assert stub.subscription_calls == []
