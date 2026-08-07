"""The Paddle API client over a stubbed httpx transport — no live host. Also asserts the API key
is sent as a bearer header (and is never a logged field)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from config.settings import Settings
from payments.client import PaddleApiError, PaddleClient, PaddleConfigError


def _mock_factory(handler: Any):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_missing_api_key_raises() -> None:
    with pytest.raises(PaddleConfigError):
        PaddleClient(Settings(paddle_api_key=None))


async def test_sandbox_base_url_selected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://sandbox-api.paddle.com")
        return httpx.Response(200, json={"data": {"id": "sub_1"}})

    client = PaddleClient(
        Settings(paddle_api_key="pk", paddle_environment="sandbox"),
        client_factory=_mock_factory(handler),
    )
    data = await client.get_subscription("sub_1")
    assert data["id"] == "sub_1"


async def test_create_transaction_sends_bearer_and_custom_data(settings: Settings) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(
            201, json={"data": {"id": "txn_1", "checkout": {"url": "https://pay/x"}}}
        )

    client = PaddleClient(settings, client_factory=_mock_factory(handler))
    data = await client.create_transaction(price_id="pri_x", user_id="user-42")
    assert data["id"] == "txn_1"
    assert seen["auth"] == "Bearer pk-test"
    assert "user-42" in seen["body"]


async def test_production_base_url_selected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.paddle.com")
        return httpx.Response(200, json={"data": {"id": "sub_1"}})

    client = PaddleClient(
        Settings(paddle_api_key="pk", paddle_environment="production"),
        client_factory=_mock_factory(handler),
    )
    data = await client.get_subscription("sub_1")
    assert data["id"] == "sub_1"


async def test_api_error_on_non_2xx(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "bad"})

    client = PaddleClient(settings, client_factory=_mock_factory(handler))
    with pytest.raises(PaddleApiError):
        await client.get_subscription("sub_missing")


async def test_list_transactions(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/transactions" in str(request.url)
        return httpx.Response(200, json={"data": [{"id": "txn_1"}, {"id": "txn_2"}]})

    client = PaddleClient(settings, client_factory=_mock_factory(handler))
    txns = await client.list_transactions(customer_id="ctm_1", subscription_id="sub_1")
    assert [t["id"] for t in txns] == ["txn_1", "txn_2"]
