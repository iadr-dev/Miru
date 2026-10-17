"""Erasing an account removes the user's rows from every table, and only that user's."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.erasure import erase_user, user_scoped_tables
from db.base import Base
from db.models import BillingEventRow, SubscriptionRow, ThreadRow, UserRow, WatchlistRow
from payments.store import SqlAlchemySubscriptionStore
from schemas.enums import Tier
from tests.scheduler.conftest import engine, insert_user, session_factory  # noqa: F401 - fixtures


async def _seed(session: AsyncSession, user_id: str) -> None:
    now = datetime.now(UTC)
    session.add(
        ThreadRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title="t",
            created_at=now,
            updated_at=now,
            last_message_at=now,
        )
    )
    session.add(
        WatchlistRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name="w",
            market="TW",
            asset_class="stock",
            sort_order="a0",
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()


async def _count(session: AsyncSession, model: type, user_id: str) -> int:
    column = model.user_id  # type: ignore[attr-defined]
    return int(
        (
            await session.execute(select(func.count()).select_from(model).where(column == user_id))
        ).scalar_one()
    )


async def test_erasing_removes_the_users_rows_everywhere_and_leaves_other_users(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811 - the imported fixture
) -> None:
    doomed = await insert_user(session_factory, tier=Tier.PRO)
    kept = await insert_user(session_factory, tier=Tier.FREE)
    async with session_factory() as session:
        await _seed(session, doomed)
        await _seed(session, kept)
        await session.commit()

        removed = await erase_user(session, doomed)
        await session.commit()

        assert removed["threads"] == 1
        assert removed["watchlists"] == 1
        assert removed["users"] == 1
        assert await _count(session, ThreadRow, doomed) == 0
        assert await _count(session, WatchlistRow, doomed) == 0
        assert await session.get(UserRow, doomed) is None
        assert await _count(session, ThreadRow, kept) == 1
        assert await session.get(UserRow, kept) is not None


async def _record(session: AsyncSession, event_id: str, data: dict[str, object]) -> None:
    """Write an event the way the webhook handler does: the Paddle `data` object, as is."""
    await SqlAlchemySubscriptionStore(session).record_event(
        event_id=event_id,
        event_type="subscription.updated",
        occurred_at=datetime.now(UTC),
        raw_payload=data,
    )


def _subscription(user_id: str, *, sub: str, customer: str) -> SubscriptionRow:
    now = datetime.now(UTC)
    return SubscriptionRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        paddle_subscription_id=sub,
        paddle_customer_id=customer,
        status="active",
        price_id="pri_x",
        tier="pro",
        updated_from_event_at=now,
        created_at=now,
        updated_at=now,
    )


async def _left(session: AsyncSession) -> set[str]:
    rows = await session.execute(select(BillingEventRow.paddle_event_id))
    return {event_id for (event_id,) in rows}


async def test_billing_events_about_the_user_are_scrubbed_and_others_kept(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811 - the imported fixture
) -> None:
    doomed = await insert_user(session_factory, tier=Tier.PRO)
    kept = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        session.add(_subscription(doomed, sub="sub_doomed", customer="ctm_doomed"))
        session.add(_subscription(kept, sub="sub_kept", customer="ctm_kept"))
        await session.flush()
        # The stored payload is the Paddle `data` object itself, not wrapped in another key.
        await _record(
            session, "evt_created", {"id": "sub_doomed", "custom_data": {"user_id": doomed}}
        )
        await _record(session, "evt_updated", {"id": "sub_doomed", "customer_id": "ctm_doomed"})
        await _record(session, "evt_txn", {"id": "txn_1", "subscription_id": "sub_doomed"})
        await _record(session, "evt_customer", {"id": "ctm_doomed", "email": "d@example.test"})
        await _record(session, "evt_address", {"id": "add_1", "customer_id": "ctm_doomed"})
        await _record(session, "evt_other", {"id": "sub_kept", "custom_data": {"user_id": kept}})
        await _record(session, "evt_other_txn", {"id": "txn_2", "subscription_id": "sub_kept"})
        await _record(session, "evt_bare", {"id": "x"})
        await session.commit()

        removed = await erase_user(session, doomed)
        await session.commit()

        left = await _left(session)
    assert removed["billing_events"] == 5
    assert left == {"evt_other", "evt_other_txn", "evt_bare"}


async def test_an_event_naming_another_user_is_never_deleted_by_a_shared_id(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    doomed = await insert_user(session_factory, tier=Tier.PRO)
    other = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        session.add(_subscription(doomed, sub="sub_doomed", customer="ctm_shared"))
        session.add(_subscription(other, sub="sub_other", customer="ctm_shared"))  # same customer
        await session.flush()
        # Mentions the doomed user's subscription id but names another user: not theirs to delete.
        await _record(
            session, "evt_names_other", {"id": "sub_doomed", "custom_data": {"user_id": other}}
        )
        await _record(
            session, "evt_theirs", {"id": "sub_doomed", "custom_data": {"user_id": doomed}}
        )
        # Only the shared customer id links this to the doomed user: ambiguous, so it is kept.
        await _record(session, "evt_shared_customer", {"id": "add_9", "customer_id": "ctm_shared"})
        # Their own subscription id, no custom_data: unambiguous, so it goes.
        await _record(session, "evt_sub_only", {"id": "sub_doomed"})
        await session.commit()

        await erase_user(session, doomed)
        await session.commit()

        left = await _left(session)
    assert left == {"evt_names_other", "evt_shared_customer"}


def test_every_table_keyed_by_a_user_is_covered() -> None:
    """A table whose per-user key is not called `user_id` would silently survive an erasure."""
    # `oauth_authorization_requests.bound_user_id` is purged explicitly by `erase_user`.
    covered = set(user_scoped_tables()) | {"users", "oauth_authorization_requests"}
    uncovered = [
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in covered and any("user" in column.name for column in table.c)
    ]
    assert uncovered == [], f"tables with a user-ish column not purged on erasure: {uncovered}"
