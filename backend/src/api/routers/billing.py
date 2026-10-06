"""Paddle billing endpoints: plan catalog, checkout creation, current subscription (reconcile-
on-read), the hosted management portal, the signature-verified webhook (the entitlement source of
truth), and billing history proxied from Paddle.

The webhook route reads the *raw request bytes* before any parsing — the Paddle signature is an
HMAC over the exact body, and re-serializing a parsed model would change the bytes and break
verification. Every other route requires a signed-in user; the webhook does not (it is
authenticated by the signature, not a session).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher, SecretEncryptionNotConfiguredError
from accounts.models import User
from accounts.secrets import SqlAlchemySecretStore
from accounts.store import SqlAlchemyUserStore
from api.dependencies import (
    get_session,
    get_settings_dep,
    require_user,
)
from api.errors import ApiError
from config.settings import Settings
from payments.client import PaddleApiError, PaddleClient, PaddleConfigError
from payments.models import SubscriptionRecord
from payments.plans import (
    BillingInterval,
    PlanNotConfiguredError,
    all_plans,
    price_id_for,
)
from payments.pricing import PricedPlan, PriceLookup, priced_plans
from payments.service import BillingService, effective_byok_active
from payments.store import SqlAlchemySubscriptionStore
from payments.webhooks import SignatureVerificationError, parse_event, verify_signature
from schemas.enums import Tier

router = APIRouter(tags=["billing"])


def _client_or_503(settings: Settings) -> PaddleClient:
    try:
        return PaddleClient(settings)
    except PaddleConfigError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="billing_unconfigured",
            message="Billing is not configured.",
            retryable=False,
        ) from exc


def _service(
    session: AsyncSession, settings: Settings, *, client: PaddleClient | None = None
) -> BillingService:
    return BillingService(
        store=SqlAlchemySubscriptionStore(session),
        user_store=SqlAlchemyUserStore(session),
        settings=settings,
        client=client,
    )


class PlansResponse(BaseModel):
    client_token: str | None
    plans: list[PricedPlan]


def get_price_lookup(settings: Settings = Depends(get_settings_dep)) -> PriceLookup | None:
    """How to read prices from Paddle, or None when billing has no API key. A dependency so tests
    (and any alternative price source) never need a live Paddle call."""
    try:
        return PaddleClient(settings).list_prices
    except PaddleConfigError:
        return None


@router.get("/billing/plans")
async def billing_plans(
    country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
    settings: Settings = Depends(get_settings_dep),
    lookup: PriceLookup | None = Depends(get_price_lookup),
) -> PlansResponse:
    """The tier/interval/byok to price_id catalog for the pricing page, plus the publishable
    Paddle.js client token. Each plan carries its price as Paddle states it (minor units, with the
    country override when `country` names a country Paddle prices separately); it is omitted,
    never guessed, when Paddle can't be asked."""
    plans = await priced_plans(all_plans(settings), lookup, country=country)
    return PlansResponse(client_token=settings.paddle_client_token, plans=plans)


class CheckoutRequest(BaseModel):
    tier: Tier
    interval: BillingInterval
    byok: bool = False


class CheckoutResponse(BaseModel):
    transaction_id: str
    checkout_url: str | None


@router.post("/billing/checkout")
async def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings_dep),
) -> CheckoutResponse:
    """Create a Paddle checkout transaction for the selected plan, tagged with the user id so the
    webhook can attribute the resulting subscription back to this account."""
    try:
        price_id = price_id_for(body.tier, body.interval, byok=body.byok, settings=settings)
    except PlanNotConfiguredError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="unknown_plan",
            message="That plan is not available.",
            retryable=False,
        ) from exc
    client = _client_or_503(settings)
    try:
        data = await client.create_transaction(price_id=price_id, user_id=user.id)
    except PaddleApiError as exc:
        raise ApiError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="billing_provider_error",
            message="Could not start checkout.",
            retryable=True,
        ) from exc
    checkout: dict[str, Any] = data.get("checkout") or {}
    checkout_url = checkout.get("url")
    return CheckoutResponse(
        transaction_id=str(data.get("id", "")),
        checkout_url=str(checkout_url) if checkout_url else None,
    )


class SubscriptionResponse(BaseModel):
    subscription: SubscriptionRecord | None
    byok_active: bool


@router.get("/billing/subscription")
async def current_subscription(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubscriptionResponse:
    """The caller's current subscription, reconciled against the live Paddle API so a dropped or
    delayed webhook self-heals on this read. A user with no subscription simply gets `null`."""
    client: PaddleClient | None = None
    try:
        client = PaddleClient(settings)
    except PaddleConfigError:
        client = None  # No live reconcile available; fall back to the local row.
    service = _service(session, settings, client=client)
    try:
        record = await service.reconcile_subscription(user_id=user.id)
    except PaddleApiError:
        record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    byok = False
    if record is not None:
        byok = effective_byok_active(
            subscription_byok=record.byok,
            has_llm_key=await _has_llm_key(session, settings, user_id=user.id),
        )
    return SubscriptionResponse(subscription=record, byok_active=byok)


class PortalResponse(BaseModel):
    portal_url: str | None


@router.post("/billing/portal")
async def billing_portal(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> PortalResponse:
    """The Paddle-hosted management URL (update card / cancel / invoices). Resolved from the live
    subscription's `management_urls`; card handling, cancellation and invoices are all Paddle's."""
    store = SqlAlchemySubscriptionStore(session)
    record = await store.get_for_user(user_id=user.id)
    if record is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="no_subscription",
            message="No subscription to manage.",
            retryable=False,
        )
    client = _client_or_503(settings)
    try:
        data = await client.get_subscription(record.paddle_subscription_id)
    except PaddleApiError as exc:
        raise ApiError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="billing_provider_error",
            message="Could not resolve the management portal.",
            retryable=True,
        ) from exc
    urls: dict[str, Any] = data.get("management_urls") or {}
    portal_url = urls.get("update_payment_method") or urls.get("cancel")
    return PortalResponse(portal_url=str(portal_url) if portal_url else None)


@router.post("/billing/webhook/paddle")
async def paddle_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, bool]:
    """Process a Paddle webhook — the entitlement source of truth. Verifies the signature over the
    raw body first, then applies the idempotent, order-safe entitlement mapping."""
    raw_body = await request.body()
    if not settings.paddle_webhook_secret:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="billing_unconfigured",
            message="Billing webhook is not configured.",
            retryable=False,
        )
    try:
        verify_signature(
            raw_body, request.headers.get("Paddle-Signature"), settings.paddle_webhook_secret
        )
    except SignatureVerificationError as exc:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_signature",
            message="Webhook signature verification failed.",
            retryable=False,
        ) from exc
    event = parse_event(raw_body)
    service = _service(session, settings)
    await service.handle_webhook_event(event)
    return {"processed": True}


@router.get("/billing/history")
async def billing_history(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, list[dict[str, object]]]:
    """Proxy Paddle's transactions API for the caller's subscription — Paddle owns the tax-
    compliant invoices/PDFs, so nothing is mirrored locally."""
    record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    if record is None:
        return {"transactions": []}
    client = _client_or_503(settings)
    try:
        transactions = await client.list_transactions(
            customer_id=record.paddle_customer_id,
            subscription_id=record.paddle_subscription_id,
        )
    except PaddleApiError as exc:
        raise ApiError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="billing_provider_error",
            message="Could not load billing history.",
            retryable=True,
        ) from exc
    return {"transactions": transactions}


async def _has_llm_key(session: AsyncSession, settings: Settings, *, user_id: str) -> bool:
    try:
        cipher = SecretCipher.from_settings(settings)
    except SecretEncryptionNotConfiguredError:
        return False
    return await SqlAlchemySecretStore(session, cipher).has_llm_key(user_id=user_id)


__all__ = ["router"]
