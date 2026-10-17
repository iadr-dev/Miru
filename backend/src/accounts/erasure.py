"""Account erasure (GDPR/PDPA "delete my account and all data").

Most per-user tables carry a `user_id` that is a bare indexed column rather than a foreign key to
`users` (the isolation-scoping design in `db/models/`), so deleting the `users` row would leave
those rows behind. This purges every table that has a `user_id` column - discovered from the ORM
metadata, so a table added later is covered without remembering to list it - and then the user.
Cancelling an external billing subscription is the caller's job and must happen first.

`billing_events` (the Paddle webhook ledger) has no user column, but each stored envelope names the
user (`custom_data.user_id`), the subscription and the customer, so those events are scrubbed too.
Uploaded attachments are not stored as files: they are processed in memory and only their
extracted text and metadata are persisted, in `messages`, which is purged with the rest."""

from __future__ import annotations

from typing import cast

import structlog
from sqlalchemy import ColumnElement, CursorResult, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import Base
from db.models import BillingEventRow, OAuthAuthorizationRequestRow, SubscriptionRow, UserRow

logger = structlog.get_logger(__name__)

_USER_COLUMN = "user_id"


def user_scoped_tables() -> list[str]:
    """Names of every table that stores rows per user, children before parents."""
    return [
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if _USER_COLUMN in table.c and table.name != UserRow.__tablename__
    ]


async def _erase_billing_events(session: AsyncSession, user_id: str) -> int:
    """Delete the webhook envelopes that belong to this user.

    `billing_events.raw_payload` holds the Paddle event's `data` object itself (see
    `BillingService.handle_webhook_event`), so `id`, `subscription_id`, `customer_id` and
    `custom_data` are top-level keys. An event is the user's if it names them in
    `custom_data.user_id`, or concerns one of their subscriptions or customers (later events for a
    subscription often omit `custom_data`).

    Two guards against deleting someone else's record: an event that names a *different* user is
    never matched by id, and a customer id that another user's subscription also references (Paddle
    reuses a customer for the same email) is not matched at all. Must run before the subscription
    rows are deleted, which is where the ids come from."""
    own = (
        await session.execute(
            select(
                SubscriptionRow.paddle_subscription_id, SubscriptionRow.paddle_customer_id
            ).where(SubscriptionRow.user_id == user_id)
        )
    ).all()
    subscription_ids = {row[0] for row in own if row[0]}
    customer_ids = {row[1] for row in own if row[1]}
    if customer_ids:
        shared = (
            await session.execute(
                select(SubscriptionRow.paddle_customer_id).where(
                    SubscriptionRow.paddle_customer_id.in_(customer_ids),
                    SubscriptionRow.user_id != user_id,
                )
            )
        ).scalars()
        customer_ids -= set(shared)

    payload = BillingEventRow.raw_payload
    named_user = payload["custom_data"]["user_id"].as_string()
    by_id: list[ColumnElement[bool]] = []
    if subscription_ids:
        by_id.append(payload["id"].as_string().in_(subscription_ids))
        by_id.append(payload["subscription_id"].as_string().in_(subscription_ids))
    if customer_ids:
        by_id.append(payload["customer_id"].as_string().in_(customer_ids))
        by_id.append(payload["id"].as_string().in_(customer_ids))  # customer.* events
    conditions = [named_user == user_id]
    if by_id:
        conditions.append(and_(or_(*by_id), or_(named_user.is_(None), named_user == user_id)))
    result = await session.execute(delete(BillingEventRow).where(or_(*conditions)))
    return int(cast("CursorResult[tuple[()]]", result).rowcount or 0)


async def erase_user(session: AsyncSession, user_id: str) -> dict[str, int]:
    """Delete everything stored for `user_id`, then the user. Returns rows removed per table
    (tables with none are omitted). The caller commits."""
    removed: dict[str, int] = {}
    events = await _erase_billing_events(session, user_id)  # reads subscriptions: before they go
    if events:
        removed[BillingEventRow.__tablename__] = events
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    for name in user_scoped_tables():
        result = await session.execute(
            delete(tables[name]).where(tables[name].c.user_id == user_id)
        )
        count = int(cast("CursorResult[tuple[()]]", result).rowcount or 0)
        if count:
            removed[name] = count
    # Pending MCP sign-in requests name the user who opened them in `bound_user_id` (not
    # `user_id`: the row exists before any user does), so the loop above does not see them.
    pending = await session.execute(
        delete(OAuthAuthorizationRequestRow).where(
            OAuthAuthorizationRequestRow.bound_user_id == user_id
        )
    )
    pending_count = int(cast("CursorResult[tuple[()]]", pending).rowcount or 0)
    if pending_count:
        removed[OAuthAuthorizationRequestRow.__tablename__] = pending_count
    users = await session.execute(delete(UserRow).where(UserRow.id == user_id))
    deleted_users = int(cast("CursorResult[tuple[()]]", users).rowcount or 0)
    if deleted_users:
        removed[UserRow.__tablename__] = deleted_users
    logger.info("account_erased", user_id=user_id, tables=len(removed))
    return removed
