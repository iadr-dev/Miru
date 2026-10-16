"""Persistence for 策略盯盤 subscriptions (one per user) and the per-day delivery count.

Kept apart from `SchedulerStore` so that protocol does not grow for a feature with its own table;
it shares the session and the notification rows (the dedupe guard) with it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import NotificationRow, StrategySubscriptionRow
from scheduler.models import (
    DeliveryChannelKind,
    NotificationSource,
    QuietHours,
    StrategySubscription,
)
from schemas.strategy_watch import STRATEGY_KEYS, StrategyKey

# 策略盯盤 notifications are recorded as alerts (the notification `source` CHECK is unchanged, so an
# existing database needs no migration); this prefix on `source_id` tells them apart.
STRATEGY_SOURCE_PREFIX = "strategy:"


class StaleSubscriptionError(Exception):
    """The subscription changed since the caller read it (optimistic version check)."""


def _to_model(row: StrategySubscriptionRow) -> StrategySubscription:
    known = set(STRATEGY_KEYS)
    strategies: tuple[StrategyKey, ...] = tuple(
        key
        for key in STRATEGY_KEYS
        if key in known and key in row.strategies  # stored order is not trusted
    )
    quiet = (
        QuietHours(start=row.quiet_start, end=row.quiet_end)
        if row.quiet_start is not None and row.quiet_end is not None
        else None
    )
    return StrategySubscription(
        id=row.id,
        user_id=row.user_id,
        strategies=strategies or STRATEGY_KEYS,
        watchlist_id=row.watchlist_id,
        morning_list=row.morning_list,
        channels=tuple(DeliveryChannelKind(c) for c in row.channels),
        quiet_hours=quiet,
        daily_limit=row.daily_limit,
        enabled=row.enabled,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class StrategySubscriptionStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, user_id: str) -> StrategySubscription | None:
        row = await self._row(user_id)
        return _to_model(row) if row is not None else None

    async def _row(self, user_id: str, *, lock: bool = False) -> StrategySubscriptionRow | None:
        statement = select(StrategySubscriptionRow).where(
            StrategySubscriptionRow.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def put(  # noqa: PLR0913 - one keyword per subscription field
        self,
        *,
        user_id: str,
        strategies: tuple[StrategyKey, ...],
        watchlist_id: str | None,
        morning_list: bool,
        channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None,
        daily_limit: int | None,
        enabled: bool,
        expected_version: int | None = None,
    ) -> StrategySubscription:
        """Create or replace the user's subscription. `expected_version` (when given) must match
        the stored version, so two devices editing at once get a conflict, not a silent
        overwrite."""
        now = datetime.now(UTC)
        row = await self._row(user_id, lock=True)
        if row is None:
            if expected_version not in (None, 0):
                raise StaleSubscriptionError(user_id)
            row = StrategySubscriptionRow(
                id=str(uuid.uuid4()), user_id=user_id, version=0, created_at=now
            )
            self._session.add(row)
        elif expected_version is not None and expected_version != row.version:
            raise StaleSubscriptionError(user_id)
        else:
            row.version += 1
        row.strategies = list(strategies)
        row.watchlist_id = watchlist_id
        row.morning_list = morning_list
        row.channels = [c.value for c in channels]
        row.quiet_start = quiet_hours.start if quiet_hours else None
        row.quiet_end = quiet_hours.end if quiet_hours else None
        row.daily_limit = daily_limit
        row.enabled = enabled
        row.updated_at = now
        await self._session.flush()
        return _to_model(row)

    async def delete(self, *, user_id: str) -> bool:
        row = await self._row(user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def enabled(self) -> list[StrategySubscription]:
        result = await self._session.execute(
            select(StrategySubscriptionRow).where(StrategySubscriptionRow.enabled.is_(True))
        )
        return [_to_model(row) for row in result.scalars().all()]

    async def alerts_sent(self, *, user_id: str, day: date) -> int:
        """Condition-met alerts already recorded for `day` (the in-app copy, one per alert),
        excluding the morning list, for the subscription's daily limit. 策略盯盤 notifications are
        alerts whose `source_id` starts with `STRATEGY_SOURCE_PREFIX`."""
        count = await self._session.scalar(
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.source == NotificationSource.ALERT.value,
                NotificationRow.source_id.like(f"{STRATEGY_SOURCE_PREFIX}%"),
                NotificationRow.channel == DeliveryChannelKind.IN_APP.value,
                NotificationRow.dedupe_key.like(f"%:{day.isoformat()}:in_app"),
                NotificationRow.source_id.not_like("%:morning"),
            )
        )
        return int(count or 0)


__all__ = ["STRATEGY_SOURCE_PREFIX", "StaleSubscriptionError", "StrategySubscriptionStore"]
