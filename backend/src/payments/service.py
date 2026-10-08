"""Billing orchestration: turn a verified Paddle webhook into an entitlement change, and
reconcile the local subscription row against the live Paddle API on read.

The webhook is the ONLY writer of `users.tier`/`byok`. `handle_webhook_event` dispatches on
`event_type`, maps the subscription's `price_id` to a tier via `plans.resolve_plan`, records the
event for idempotency, applies the ordering-guarded upsert, and — only when the upsert actually
took effect — writes the derived tier cache on `users`. A `scheduled_change: cancel` is recorded
(so the UI can show "cancels on <date>") but does NOT downgrade; only the actual
`subscription.canceled` event drops the tier to free. `subscription.past_due` is a grace state:
the row's status changes but the tier is left at the plan tier so entitlements are unchanged
during Paddle's dunning window. `subscription.paused` holds the paid tier back (billing is
stopped, so there is nothing to entitle) and `subscription.resumed` restores it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from accounts.models import User
from accounts.store import UserStore
from config.settings import Settings
from payments.client import PaddleClient
from payments.models import PaddleSubscriptionEntity, SubscriptionRecord
from payments.plans import UnknownPriceError, resolve_plan
from payments.store import SubscriptionStore, SubscriptionUpdate
from payments.webhooks import PaddleEvent
from schemas.enums import Tier

logger = structlog.get_logger(__name__)

# Events that establish or re-map an active subscription (the price_id drives tier/byok).
_ENTITLING_EVENTS = frozenset(
    {
        "subscription.created",
        "subscription.activated",
        "subscription.updated",
        "subscription.resumed",
    }
)
_CANCEL_EVENT = "subscription.canceled"
_PAST_DUE_EVENT = "subscription.past_due"
_PAUSE_EVENT = "subscription.paused"
_HANDLED_EVENTS = _ENTITLING_EVENTS | {_CANCEL_EVENT, _PAST_DUE_EVENT, _PAUSE_EVENT}
# Statuses with nothing to entitle: the subscription has ended, or billing is stopped.
_NO_ENTITLEMENT_STATUSES = frozenset({"canceled", "paused"})
# A checkout transaction only proves a purchase once it has been paid.
_PAID_TRANSACTION_STATUSES = frozenset({"paid", "completed"})


# How long a failed payment keeps the paid tier while Paddle retries it, counted from the moment the
# subscription went past_due. After that the tier lapses to free until a payment goes through.
PAST_DUE_GRACE = timedelta(days=7)


def _past_due_lapsed(past_due_since: datetime | None, *, now: datetime | None = None) -> bool:
    if past_due_since is None:
        return False
    since = past_due_since if past_due_since.tzinfo else past_due_since.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - since > PAST_DUE_GRACE


def _tier_for(status: str, plan_tier: Tier, *, past_due_since: datetime | None = None) -> Tier:
    """The tier a subscription in `status` grants: its plan's, unless it has ended, is paused, or
    has been past_due for longer than `PAST_DUE_GRACE` (a grace window while Paddle retries)."""
    if status in _NO_ENTITLEMENT_STATUSES:
        return Tier.FREE
    if status == "past_due" and _past_due_lapsed(past_due_since):
        return Tier.FREE
    return plan_tier


def effective_byok_active(*, subscription_byok: bool, has_llm_key: bool) -> bool:
    """Resolve whether BYOK is genuinely active for entitlement purposes.

    A BYOK subscription only lifts the chat limit while the user actually has a key on file. If a
    BYOK subscriber removes all their keys, this returns `False` so they fall back to the standard
    fair-use limit rather than being silently billed the higher standard rate — the coupling the
    tier model requires."""
    return subscription_byok and has_llm_key


class BillingService:
    def __init__(
        self,
        *,
        store: SubscriptionStore,
        user_store: UserStore,
        settings: Settings,
        client: PaddleClient | None = None,
    ) -> None:
        self._store = store
        self._user_store = user_store
        self._settings = settings
        self._client = client

    async def handle_webhook_event(self, event: PaddleEvent) -> None:
        """Process one verified webhook. Idempotent (redelivery is a no-op) and order-safe (an
        older event never regresses a newer state)."""
        first_time = await self._store.record_event(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            raw_payload=event.data,
        )
        if not first_time:
            return  # Already processed — idempotent no-op.

        if event.event_type not in _HANDLED_EVENTS:
            logger.info("billing_event_ignored", event_type=event.event_type)
            return

        entity = PaddleSubscriptionEntity.model_validate(event.data)
        user_id = await self._resolve_user_id(entity)
        if user_id is None:
            logger.warning("billing_event_no_user", paddle_subscription_id=entity.id)
            return

        try:
            plan = resolve_plan(entity.first_price_id() or "", self._settings)
        except UnknownPriceError:
            logger.warning("billing_event_unknown_price", paddle_subscription_id=entity.id)
            return

        # Cancel and pause drop the derived tier to free; every other handled event keeps the plan
        # tier (past_due is a grace state - entitlements unchanged - and a scheduled cancel stays
        # active until the cancel event itself fires).
        if event.event_type == _CANCEL_EVENT:
            status = "canceled"
        elif event.event_type == _PAUSE_EVENT:
            status = "paused"
        elif event.event_type == _PAST_DUE_EVENT:
            status = "past_due"
        else:
            status = entity.status
        derived_tier = _tier_for(status, plan.tier)

        update = SubscriptionUpdate(
            user_id=user_id,
            paddle_subscription_id=entity.id,
            paddle_customer_id=entity.customer_id,
            status=status,
            price_id=plan.price_id,
            tier=derived_tier,
            byok=plan.byok,
            current_period_start=entity.period_start(),
            current_period_end=entity.period_end(),
            scheduled_cancel_at=entity.scheduled_cancel_at(),
            occurred_at=event.occurred_at,
        )
        record = await self._store.upsert_from_event(update)
        if record is None:
            return  # Out-of-order event — skipped, no tier write.

        # Derived tier cache is written only here, only from the webhook-resolved subscription.
        await self._user_store.set_tier(user_id=user_id, tier=derived_tier)
        logger.info(
            "billing_entitlement_applied",
            user_id=user_id,
            event_type=event.event_type,
            tier=derived_tier.value,
        )

    async def reconcile_subscription(self, *, user_id: str) -> SubscriptionRecord | None:
        """Reconcile-on-read: fetch the live subscription from Paddle and upsert the local row so a
        dropped/delayed webhook self-heals. Returns the current record (local if there is nothing
        to reconcile against, freshly reconciled otherwise)."""
        local = await self._store.get_for_user(user_id=user_id)
        if local is None or self._client is None:
            return local

        entity_data = await self._client.get_subscription(local.paddle_subscription_id)
        entity = PaddleSubscriptionEntity.model_validate(entity_data)
        try:
            plan = resolve_plan(entity.first_price_id() or "", self._settings)
        except UnknownPriceError:
            return local

        derived_tier = _tier_for(
            entity.status,
            plan.tier,
            past_due_since=local.updated_from_event_at if local.status == "past_due" else None,
        )
        # Reconcile stamps "now" as the applied point via the record's own occurred marker; the
        # live API is authoritative, so it is never treated as out of order against itself.
        update = SubscriptionUpdate(
            user_id=user_id,
            paddle_subscription_id=entity.id,
            paddle_customer_id=entity.customer_id,
            status=entity.status,
            price_id=plan.price_id,
            tier=derived_tier,
            byok=plan.byok,
            current_period_start=entity.period_start(),
            current_period_end=entity.period_end(),
            scheduled_cancel_at=entity.scheduled_cancel_at(),
            occurred_at=(
                datetime.now(UTC)
                if entity.status == "past_due" and local.status != "past_due"
                else local.updated_from_event_at
            ),
        )
        record = await self._store.upsert_from_event(update)
        if record is not None:
            await self._user_store.set_tier(user_id=user_id, tier=derived_tier)
        return record or local

    async def adopt_transaction(
        self, *, user: User, transaction_id: str
    ) -> SubscriptionRecord | None:
        """Self-heal a lost first webhook: the checkout's transaction names the subscription it
        created. Only a transaction tagged with THIS user's id is adopted, so one account can never
        claim another's purchase, and only a PAID one, and only when the subscription is not yet
        recorded (the lost-first-webhook case - an existing record is reconciled, never reset).
        Returns the local record, or `None` while the payment has not produced a subscription."""
        if self._client is None:
            return None
        transaction = await self._client.get_transaction(transaction_id)
        custom_data: dict[str, object] = transaction.get("custom_data") or {}
        subscription_id = transaction.get("subscription_id")
        if custom_data.get("user_id") != user.id or not subscription_id:
            return None
        if transaction.get("status") not in _PAID_TRANSACTION_STATUSES:
            return None
        known = await self._store.get_by_paddle_id(paddle_subscription_id=str(subscription_id))
        if known is not None:
            # Already recorded: this is no longer the lost-first-webhook case, and re-adopting
            # would restart the clocks (e.g. a past_due grace window). Reconcile instead - it
            # keeps them.
            return (
                await self.reconcile_subscription(user_id=user.id)
                if known.user_id == user.id
                else None
            )
        entity = PaddleSubscriptionEntity.model_validate(
            await self._client.get_subscription(str(subscription_id))
        )
        try:
            plan = resolve_plan(entity.first_price_id() or "", self._settings)
        except UnknownPriceError:
            return None
        tier = _tier_for(entity.status, plan.tier)
        record = await self._store.upsert_from_event(
            SubscriptionUpdate(
                user_id=user.id,
                paddle_subscription_id=entity.id,
                paddle_customer_id=entity.customer_id,
                status=entity.status,
                price_id=plan.price_id,
                tier=tier,
                byok=plan.byok,
                current_period_start=entity.period_start(),
                current_period_end=entity.period_end(),
                scheduled_cancel_at=entity.scheduled_cancel_at(),
                occurred_at=datetime.now(UTC),
            )
        )
        if record is not None:
            await self._user_store.set_tier(user_id=user.id, tier=tier)
        return record or await self._store.get_for_user(user_id=user.id)

    async def lapse_if_overdue(self, user: User) -> User:
        """Drop a paid user to free once their payment has been failing past the grace window.
        Cheap for the common case (free users and healthy subscribers return immediately)."""
        if user.tier is Tier.FREE:
            return user
        record = await self._store.get_for_user(user_id=user.id)
        if record is None or record.status != "past_due":
            return user
        if not _past_due_lapsed(record.updated_from_event_at):
            return user
        logger.info("billing_past_due_lapsed", user_id=user.id)
        return await self._user_store.set_tier(user_id=user.id, tier=Tier.FREE)

    async def _resolve_user_id(self, entity: PaddleSubscriptionEntity) -> str | None:
        """The user id comes from the checkout's `custom_data`; if a later webhook omits it, fall
        back to the local row keyed by the Paddle subscription id."""
        from_custom = entity.user_id()
        if from_custom is not None:
            return from_custom
        existing = await self._store.get_by_paddle_id(paddle_subscription_id=entity.id)
        return existing.user_id if existing is not None else None


__all__ = ["BillingService", "effective_byok_active"]
