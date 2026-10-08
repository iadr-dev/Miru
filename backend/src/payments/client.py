"""A thin async client for the Paddle billing API.

Network-I/O layering judgment: the project rule "only `sources/` performs network I/O" governs
the *market-data capability* path — adapters the data-access router dispatches by
`(capability, symbol, market)`. A billing provider is not a market-data capability; routing
Paddle's transaction/subscription calls through the source-adapter abstraction would be a
category error (there is no capability to name, no symbol, no market). This module therefore
makes its own outbound HTTPS calls directly, exactly as `accounts/oauth_providers.py` does for
the Google/LINE OAuth endpoints — a product-layer module owning the outbound integration that is
specific to it. Verification stays strict (httpx's default); the client is never constructed with
verification disabled.

The client is injected an `httpx.AsyncClient` factory so tests drive it with a `MockTransport`
and never touch a live Paddle host. The `PADDLE_API_KEY` bearer is sent as an auth header and is
never logged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import structlog

from config.settings import Settings

logger = structlog.get_logger(__name__)

_SANDBOX_BASE_URL = "https://sandbox-api.paddle.com"
_PRODUCTION_BASE_URL = "https://api.paddle.com"
_HTTP_TIMEOUT_SECONDS = 15.0

HttpClientFactory = Callable[[], httpx.AsyncClient]


class PaddleConfigError(RuntimeError):
    """Paddle server-side credentials are missing — the client refuses to make a call rather than
    hit the API unauthenticated."""


class PaddleApiError(RuntimeError):
    """The Paddle API returned a non-success status."""


def _base_url_for(environment: str) -> str:
    """Select the sandbox vs production host from config — never hardcoded to one."""
    return _PRODUCTION_BASE_URL if environment == "production" else _SANDBOX_BASE_URL


def _default_client_factory() -> httpx.AsyncClient:
    # Strict TLS verification (httpx default); this billing path never downgrades it.
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)


class PaddleClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: HttpClientFactory = _default_client_factory,
    ) -> None:
        if not settings.paddle_api_key:
            raise PaddleConfigError("paddle api key not configured")
        self._api_key = settings.paddle_api_key
        self._base_url = _base_url_for(settings.paddle_environment)
        self._client_factory = client_factory

    def _auth_headers(self) -> dict[str, str]:
        # The bearer key is attached here and never bound to a log field.
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        async with self._client_factory() as client:
            response = await client.request(
                method, url, headers=self._auth_headers(), json=json, params=params
            )
        if response.status_code >= int(httpx.codes.BAD_REQUEST):
            # Log the status and path only — never the request body or the auth header.
            logger.warning("paddle_api_error", status=response.status_code, path=path)
            raise PaddleApiError(f"paddle returned HTTP {response.status_code} for {path}")
        payload: dict[str, Any] = response.json()
        return payload

    async def create_transaction(
        self, *, price_id: str, user_id: str, customer_id: str | None = None
    ) -> dict[str, Any]:
        """Create a checkout transaction for one price. `custom_data.user_id` is how the webhook
        later attributes the resulting subscription back to our user."""
        body: dict[str, Any] = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"user_id": user_id},
        }
        if customer_id:
            body["customer_id"] = customer_id
        payload = await self._request("POST", "/transactions", json=body)
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def list_prices(self, price_ids: list[str]) -> list[dict[str, Any]]:
        """Read the given prices (amount, currency, country overrides) — display only."""
        payload = await self._request(
            "GET", "/prices", params={"id": ",".join(price_ids), "per_page": "50"}
        )
        data: list[dict[str, Any]] = payload.get("data", [])
        return data

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        """Read one transaction - its `subscription_id` and `custom_data` tell which subscription a
        checkout produced and for whom."""
        payload = await self._request("GET", f"/transactions/{transaction_id}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Read the live subscription entity — the reconcile-on-read source of truth."""
        payload = await self._request("GET", f"/subscriptions/{subscription_id}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def create_portal_session(
        self, customer_id: str, subscription_ids: list[str]
    ) -> dict[str, Any]:
        """A customer-portal session: authenticated, short-lived links to the portal overview and
        to the per-subscription pages (update payment method, cancel)."""
        payload = await self._request(
            "POST",
            f"/customers/{customer_id}/portal-sessions",
            json={"subscription_ids": subscription_ids},
        )
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def change_subscription_price(
        self, subscription_id: str, *, price_id: str
    ) -> dict[str, Any]:
        """Switch a live subscription to another price. Proration is Paddle's: the difference for
        the remainder of the period is charged (or credited) immediately."""
        body: dict[str, Any] = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "proration_billing_mode": "prorated_immediately",
        }
        payload = await self._request("PATCH", f"/subscriptions/{subscription_id}", json=body)
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def resume_paused_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Resume a paused subscription right away (billing restarts; Paddle sends `resumed`)."""
        payload = await self._request(
            "POST",
            f"/subscriptions/{subscription_id}/resume",
            json={"effective_from": "immediately"},
        )
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def clear_scheduled_change(self, subscription_id: str) -> dict[str, Any]:
        """Remove a pending scheduled change (a cancel that has not taken effect yet)."""
        payload = await self._request(
            "PATCH", f"/subscriptions/{subscription_id}", json={"scheduled_change": None}
        )
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def list_transactions(
        self, *, customer_id: str | None = None, subscription_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List transactions for billing history — proxied straight to the frontend."""
        params: dict[str, str] = {}
        if customer_id:
            params["customer_id"] = customer_id
        if subscription_id:
            params["subscription_id"] = subscription_id
        payload = await self._request("GET", "/transactions", params=params or None)
        data: list[dict[str, Any]] = payload.get("data", [])
        return data

    async def get_invoice_url(self, transaction_id: str) -> str | None:
        """Resolve the tax-compliant invoice PDF URL for a transaction (a separate Paddle call)."""
        payload = await self._request("GET", f"/transactions/{transaction_id}/invoice")
        data: dict[str, Any] = payload.get("data", {})
        url = data.get("url")
        return str(url) if url else None


__all__ = [
    "HttpClientFactory",
    "PaddleApiError",
    "PaddleClient",
    "PaddleConfigError",
]
