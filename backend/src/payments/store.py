"""`SubscriptionStore` — the SQLAlchemy-backed persistence for `subscriptions` + `billing_events`.

Same per-request-session convention as the other stores: the caller owns the transaction
boundary; methods only `flush`. Two correctness guards live here rather than in the service:

- **Idempotency** — `record_event` inserts into `billing_events` guarded by the
  `paddle_event_id` unique constraint. A redelivered event is detected (already recorded) and the
  caller treats it as a no-op.
- **Ordering** — `upsert_from_event` applies a mapping only when the event's `occurred_at` is at
  least the last-applied `updated_from_event_at` on the row. A late-delivered older event (e.g. a
  stale `subscription.updated` arriving after a `subscription.canceled`) is recognized as out of
  order and skipped, so it can never resurrect or regress state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BillingEventRow, SubscriptionRow
from payments.models import SubscriptionRecord
from schemas.enums import Tier

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SubscriptionUpdate:
    """The normalized entitlement snapshot a webhook or a reconcile carries into the store."""

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
    occurred_at: datetime


def _as_utc(value: datetime) -> datetime:
    """SQLite's `DateTime` does not persist tz, so a value read back is naive even though it was
    written aware. Treat a naive value as UTC so ordering comparisons never mix naive/aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_record(row: SubscriptionRow) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=row.id,
        user_id=row.user_id,
        paddle_subscription_id=row.paddle_subscription_id,
        paddle_customer_id=row.paddle_customer_id,
        status=row.status,
        price_id=row.price_id,
        tier=Tier(row.tier),
        byok=row.byok,
        current_period_start=row.current_period_start,
        current_period_end=row.current_period_end,
        scheduled_cancel_at=row.scheduled_cancel_at,
        updated_from_event_at=row.updated_from_event_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@runtime_checkable
class SubscriptionStore(Protocol):
    async def record_event(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        raw_payload: dict[str, object],
    ) -> bool: ...

    async def upsert_from_event(self, update: SubscriptionUpdate) -> SubscriptionRecord | None: ...

    async def get_for_user(self, *, user_id: str) -> SubscriptionRecord | None: ...

    async def get_by_paddle_id(
        self, *, paddle_subscription_id: str
    ) -> SubscriptionRecord | None: ...


class SqlAlchemySubscriptionStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_event(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        raw_payload: dict[str, object],
    ) -> bool:
        """Record a webhook in the idempotency ledger. Returns `True` if this is the first time we
        have seen `event_id`, `False` if it was already recorded (a redelivery → the caller
        no-ops). The unique constraint on `paddle_event_id` is the real guard; the pre-check keeps
        the common path from raising."""
        existing = await self._session.execute(
            select(BillingEventRow.id).where(BillingEventRow.paddle_event_id == event_id)
        )
        if existing.first() is not None:
            logger.info("billing_event_redelivered", event_id=event_id, event_type=event_type)
            return False
        self._session.add(
            BillingEventRow(
                id=str(uuid.uuid4()),
                paddle_event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                processed_at=datetime.now(UTC),
                raw_payload=raw_payload,
            )
        )
        await self._session.flush()
        return True

    async def upsert_from_event(self, update: SubscriptionUpdate) -> SubscriptionRecord | None:
        """Apply an entitlement snapshot, honoring the ordering guard. Returns the resulting
        record, or `None` if the event was out of order and skipped."""
        result = await self._session.execute(
            select(SubscriptionRow).where(
                SubscriptionRow.paddle_subscription_id == update.paddle_subscription_id
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)

        if row is None:
            row = SubscriptionRow(
                id=str(uuid.uuid4()),
                user_id=update.user_id,
                paddle_subscription_id=update.paddle_subscription_id,
                paddle_customer_id=update.paddle_customer_id,
                status=update.status,
                price_id=update.price_id,
                tier=update.tier.value,
                byok=update.byok,
                current_period_start=update.current_period_start,
                current_period_end=update.current_period_end,
                scheduled_cancel_at=update.scheduled_cancel_at,
                updated_from_event_at=update.occurred_at,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
            await self._session.flush()
            return _row_to_record(row)

        # Ordering guard: never let an older event overwrite state applied from a newer one.
        if _as_utc(update.occurred_at) < _as_utc(row.updated_from_event_at):
            logger.info(
                "billing_event_out_of_order",
                paddle_subscription_id=update.paddle_subscription_id,
                event_occurred_at=update.occurred_at.isoformat(),
                last_applied=row.updated_from_event_at.isoformat(),
            )
            return None

        row.paddle_customer_id = update.paddle_customer_id
        row.status = update.status
        row.price_id = update.price_id
        row.tier = update.tier.value
        row.byok = update.byok
        row.current_period_start = update.current_period_start
        row.current_period_end = update.current_period_end
        row.scheduled_cancel_at = update.scheduled_cancel_at
        row.updated_from_event_at = update.occurred_at
        row.updated_at = now
        await self._session.flush()
        return _row_to_record(row)

    async def get_for_user(self, *, user_id: str) -> SubscriptionRecord | None:
        result = await self._session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id)
            .order_by(SubscriptionRow.updated_at.desc())
        )
        row = result.scalars().first()
        return _row_to_record(row) if row is not None else None

    async def get_by_paddle_id(self, *, paddle_subscription_id: str) -> SubscriptionRecord | None:
        result = await self._session.execute(
            select(SubscriptionRow).where(
                SubscriptionRow.paddle_subscription_id == paddle_subscription_id
            )
        )
        row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None


__all__ = [
    "SqlAlchemySubscriptionStore",
    "SubscriptionStore",
    "SubscriptionUpdate",
]
