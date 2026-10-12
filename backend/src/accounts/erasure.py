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
from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import Base
from db.models import BillingEventRow, SubscriptionRow, UserRow

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

    An envelope is theirs if it names them in `custom_data.user_id`, or concerns one of their
    subscriptions or customer ids (later events for a subscription often omit `custom_data`).
    Must run before the subscription rows are deleted, which is where those ids come from."""
    rows = (
        await session.execute(
            select(
                SubscriptionRow.paddle_subscription_id, SubscriptionRow.paddle_customer_id
            ).where(SubscriptionRow.user_id == user_id)
        )
    ).all()
    subscription_ids = {row[0] for row in rows if row[0]}
    customer_ids = {row[1] for row in rows if row[1]}
    data = BillingEventRow.raw_payload["data"]
    conditions = [data["custom_data"]["user_id"].as_string() == user_id]
    if subscription_ids:
        conditions.append(data["id"].as_string().in_(subscription_ids))
        conditions.append(data["subscription_id"].as_string().in_(subscription_ids))
    if customer_ids:
        conditions.append(data["customer_id"].as_string().in_(customer_ids))
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
    users = await session.execute(delete(UserRow).where(UserRow.id == user_id))
    deleted_users = int(cast("CursorResult[tuple[()]]", users).rowcount or 0)
    if deleted_users:
        removed[UserRow.__tablename__] = deleted_users
    logger.info("account_erased", user_id=user_id, tables=len(removed))
    return removed
