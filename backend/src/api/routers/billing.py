"""Paddle billing endpoints: plan catalog, checkout creation, current subscription (reconcile-
on-read), the hosted management portal, the signature-verified webhook (the entitlement source of
truth), and billing history proxied from Paddle.

The webhook route reads the *raw request bytes* before any parsing — the Paddle signature is an
HMAC over the exact body, and re-serializing a parsed model would change the bytes and break
verification. Every other route requires a signed-in user; the webhook does not (it is
authenticated by the signature, not a session).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.byok import byok_active
from accounts.entitlements import TierSummary, tier_catalog
from accounts.models import User
from accounts.store import SqlAlchemyUserStore
from api.dependencies import (
    get_session,
    get_settings_dep,
    require_user,
    system_scope,
)
from api.errors import ApiError
from config.settings import PaddleEnvironment, Settings
from payments.client import PaddleApiError, PaddleClient, PaddleConfigError
from payments.models import SubscriptionRecord
from payments.plans import (
    BillingInterval,
    PlanNotConfiguredError,
    all_plans,
    price_id_for,
)
from payments.pricing import PricedPlan, PriceLookup, priced_plans
from payments.service import BillingService
from payments.store import SqlAlchemySubscriptionStore
from payments.transactions import BillingTransaction, project_transaction
from payments.webhooks import SignatureVerificationError, parse_event, verify_signature
from schemas.enums import Tier

router = APIRouter(tags=["billing"])


def _unconfigured() -> ApiError:
    return ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="billing_unconfigured",
        message="Billing is not configured.",
        retryable=False,
    )


def _client_or_503(settings: Settings) -> PaddleClient:
    try:
        return PaddleClient(settings)
    except PaddleConfigError as exc:
        raise _unconfigured() from exc


def get_billing_client(settings: Settings = Depends(get_settings_dep)) -> PaddleClient:
    """The Paddle API client for the subscription-management routes; a dependency so tests can
    substitute a stub transport."""
    return _client_or_503(settings)


def get_optional_billing_client(
    settings: Settings = Depends(get_settings_dep),
) -> PaddleClient | None:
    """Like `get_billing_client`, but `None` (instead of a 503) when billing has no API key: the
    subscription read then simply serves the local row."""
    try:
        return PaddleClient(settings)
    except PaddleConfigError:
        return None


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
    # `sandbox` | `production` - which Paddle the browser's Paddle.js must talk to.
    environment: PaddleEnvironment
    plans: list[PricedPlan]
    # What each tier includes (limits + features), for the side-by-side comparison.
    tiers: list[TierSummary]


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
    return PlansResponse(
        client_token=settings.paddle_client_token,
        environment=settings.paddle_environment,
        plans=plans,
        tiers=tier_catalog(),
    )


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
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> CheckoutResponse:
    """Create a Paddle checkout transaction for the selected plan, tagged with the user id so the
    webhook can attribute the resulting subscription back to this account. A user who already has a
    live subscription changes or cancels it in the Paddle portal instead - a second checkout would
    bill them twice."""
    existing = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    if existing is not None and existing.status != "canceled":
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="already_subscribed",
            message="You already have a subscription; manage it from the billing portal.",
            retryable=False,
        )
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
    client: PaddleClient | None = Depends(get_optional_billing_client),
) -> SubscriptionResponse:
    """The caller's current subscription, reconciled against the live Paddle API so a dropped or
    delayed webhook self-heals on this read. A user with no subscription simply gets `null`."""
    service = _service(session, settings, client=client)
    try:
        record = await service.reconcile_subscription(user_id=user.id)
    except PaddleApiError:
        record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    return await _subscription_response(session, settings, user_id=user.id, record=record)


async def _subscription_response(
    session: AsyncSession, settings: Settings, *, user_id: str, record: SubscriptionRecord | None
) -> SubscriptionResponse:
    byok = False
    if record is not None:
        byok = await byok_active(session, settings, user_id=user_id, subscription_byok=record.byok)
    return SubscriptionResponse(subscription=record, byok_active=byok)


class SyncRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=100)


@router.post("/billing/sync")
async def sync_checkout(
    body: SyncRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    client: PaddleClient = Depends(get_billing_client),
) -> SubscriptionResponse:
    """Recover a checkout whose webhook never arrived: look the transaction up at Paddle and adopt
    the subscription it created, provided the transaction was opened for this very account."""
    try:
        record = await _service(session, settings, client=client).adopt_transaction(
            user=user, transaction_id=body.transaction_id
        )
    except PaddleApiError as exc:
        raise _provider_error("Could not confirm the payment yet.") from exc
    return await _subscription_response(session, settings, user_id=user.id, record=record)


def _provider_error(message: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="billing_provider_error",
        message=message,
        retryable=True,
    )


def _conflict(code: str, message: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_409_CONFLICT, code=code, message=message, retryable=False
    )


async def _live_subscription(session: AsyncSession, user_id: str) -> SubscriptionRecord:
    record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user_id)
    if record is None or record.status == "canceled":
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="no_subscription",
            message="No active subscription.",
            retryable=False,
        )
    return record


@router.post("/billing/change-plan")
async def change_plan(
    body: CheckoutRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    client: PaddleClient = Depends(get_billing_client),
) -> SubscriptionResponse:
    """Move the caller's live subscription to another tier / interval / BYOK variant. Paddle's
    customer portal only offers card updates and cancellation, so plan changes are made here
    against Paddle's subscription API (prorated immediately by Paddle). The local row and tier are
    refreshed from Paddle straight away; the webhook that follows is then a no-op."""
    record = await _live_subscription(session, user.id)
    if record.status == "paused":
        raise _conflict("paused", "Resume your subscription before changing plan.")
    if record.scheduled_cancel_at is not None:
        raise _conflict("cancel_scheduled", "Resume your subscription before changing plan.")
    try:
        price_id = price_id_for(body.tier, body.interval, byok=body.byok, settings=settings)
    except PlanNotConfiguredError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="unknown_plan",
            message="That plan is not available.",
            retryable=False,
        ) from exc
    if price_id == record.price_id:
        raise _conflict("same_plan", "You are already on that plan.")
    try:
        await client.change_subscription_price(record.paddle_subscription_id, price_id=price_id)
        updated = await _service(session, settings, client=client).reconcile_subscription(
            user_id=user.id
        )
    except PaddleApiError as exc:
        raise _provider_error("Could not change plan.") from exc
    return await _subscription_response(session, settings, user_id=user.id, record=updated)


@router.post("/billing/resume")
async def resume_subscription(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    client: PaddleClient = Depends(get_billing_client),
) -> SubscriptionResponse:
    """Undo a scheduled cancellation, or restart a paused subscription."""
    record = await _live_subscription(session, user.id)
    if record.status != "paused" and record.scheduled_cancel_at is None:
        raise _conflict(
            "nothing_to_resume", "This subscription is neither paused nor scheduled to cancel."
        )
    try:
        if record.status == "paused":
            await client.resume_paused_subscription(record.paddle_subscription_id)
        else:
            await client.clear_scheduled_change(record.paddle_subscription_id)
        updated = await _service(session, settings, client=client).reconcile_subscription(
            user_id=user.id
        )
    except PaddleApiError as exc:
        raise _provider_error("Could not resume the subscription.") from exc
    return await _subscription_response(session, settings, user_id=user.id, record=updated)


class InvoiceResponse(BaseModel):
    url: str | None


@router.get("/billing/invoice/{transaction_id}")
async def billing_invoice(
    transaction_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    client: PaddleClient = Depends(get_billing_client),
) -> InvoiceResponse:
    """A short-lived link to Paddle's tax-compliant invoice PDF. Only for a transaction that is in
    the caller's own billing history - the id alone never grants access."""
    record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    if record is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="no_subscription",
            message="No billing history.",
            retryable=False,
        )
    try:
        own = await client.list_transactions(
            customer_id=record.paddle_customer_id,
            subscription_id=record.paddle_subscription_id,
        )
        if transaction_id not in {str(tx.get("id")) for tx in own}:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="transaction_not_found",
                message="Transaction not found.",
                retryable=False,
            )
        url = await client.get_invoice_url(transaction_id)
    except PaddleApiError as exc:
        raise _provider_error("Could not load the invoice.") from exc
    return InvoiceResponse(url=url)


class PortalResponse(BaseModel):
    portal_url: str | None


@router.post("/billing/portal")
async def billing_portal(
    purpose: Literal["overview", "payment"] = Query(default="overview"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    client: PaddleClient = Depends(get_billing_client),
) -> PortalResponse:
    """A link into Paddle's customer portal. `overview` is the portal home (plan, payments,
    cancel); `payment` goes straight to the update-payment-method form (the past-due fix). Card
    handling, cancellation and invoices are all Paddle's. Falls back to the subscription's own
    management links if a portal session can't be created."""
    record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    if record is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="no_subscription",
            message="No subscription to manage.",
            retryable=False,
        )
    try:
        url = await _portal_url(client, record, purpose)
    except PaddleApiError as exc:
        raise _provider_error("Could not resolve the management portal.") from exc
    return PortalResponse(portal_url=url)


async def _portal_url(
    client: PaddleClient, record: SubscriptionRecord, purpose: Literal["overview", "payment"]
) -> str | None:
    if record.paddle_customer_id:
        try:
            created = await client.create_portal_session(
                record.paddle_customer_id, [record.paddle_subscription_id]
            )
        except PaddleApiError:
            created = {}  # fall through to the subscription's own management links
        urls: dict[str, Any] = created.get("urls") or {}
        if purpose == "overview":
            general: dict[str, Any] = urls.get("general") or {}
            overview = general.get("overview")
            if overview:
                return str(overview)
        else:
            subscriptions: list[dict[str, Any]] = urls.get("subscriptions") or []
            pay = (
                subscriptions[0].get("update_subscription_payment_method")
                if subscriptions
                else None
            )
            if pay:
                return str(pay)
    data = await client.get_subscription(record.paddle_subscription_id)
    links: dict[str, Any] = data.get("management_urls") or {}
    fallback = links.get("update_payment_method") or links.get("cancel")
    return str(fallback) if fallback else None


@router.post("/billing/webhook/paddle", dependencies=[Depends(system_scope)])
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
            raw_body,
            request.headers.get("Paddle-Signature"),
            settings.paddle_webhook_secret.get_secret_value(),
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


class BillingHistoryResponse(BaseModel):
    transactions: list[BillingTransaction]


@router.get("/billing/history")
async def billing_history(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    client: PaddleClient | None = Depends(get_optional_billing_client),
) -> BillingHistoryResponse:
    """The caller's Paddle transactions, reduced to what the billing page shows. Paddle owns the
    tax-compliant invoices/PDFs, so nothing is mirrored locally."""
    record = await SqlAlchemySubscriptionStore(session).get_for_user(user_id=user.id)
    if record is None:
        return BillingHistoryResponse(transactions=[])
    if client is None:
        raise _unconfigured()
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
    rows = (project_transaction(raw) for raw in transactions)
    return BillingHistoryResponse(transactions=[row for row in rows if row is not None])


__all__ = ["router"]
