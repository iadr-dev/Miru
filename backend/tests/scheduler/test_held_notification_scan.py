"""The quiet-hours release scan finds held rows even behind many already-sent ones."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

import scheduler.store as store_module
from scheduler.models import DeliveryChannelKind, NotificationSource
from scheduler.store import SqlAlchemySchedulerStore
from schemas.enums import Tier
from tests.scheduler.conftest import insert_user


async def test_held_rows_behind_many_sent_rows_are_still_found(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "_HELD_SCAN_BATCH", 3)
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        for index in range(7):  # the first 7 are already sent: more than two scan batches
            await store.record_notification(
                user_id=user,
                source=NotificationSource.ALERT,
                source_id=f"sent-{index}",
                channel=DeliveryChannelKind.PUSH,
                trigger_date=date(2026, 10, 5),
                payload={"held": False},
            )
        for index in range(2):
            await store.record_notification(
                user_id=user,
                source=NotificationSource.ALERT,
                source_id=f"held-{index}",
                channel=DeliveryChannelKind.PUSH,
                trigger_date=date(2026, 10, 5),
                payload={"held": True},
            )
        await session.commit()

        found = await store.list_held_notifications(
            since=datetime.now(UTC) - timedelta(days=1), limit=2
        )

    assert sorted(n.source_id for n in found) == ["held-0", "held-1"]
