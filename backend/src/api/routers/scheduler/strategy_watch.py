"""The user's 策略盯盤 subscription (`/strategy-watch/subscription`): one per user, created or
replaced with PUT. Open to every signed-in user; a plan without intraday data gets the alerts with
the intraday values masked (modules/14 bound 2), not a refusal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import get_session, require_user
from api.errors import ApiError
from api.routers.scheduler._common import not_found as _not_found
from api.routers.scheduler._common import store as _store
from scheduler.models import DeliveryChannelKind, QuietHours, StrategySubscription
from scheduler.store import SchedulerStore
from scheduler.strategy_store import StaleSubscriptionError, StrategySubscriptionStore
from schemas.strategy_watch import StrategyKey

router = APIRouter()

MAX_DAILY_LIMIT = 50


class PutStrategySubscriptionRequest(BaseModel):
    """The whole subscription. Empty `channels` / null `quiet_hours` inherit the user's alert
    preferences; null `watchlist_id` means every stock on today's screen. `expected_version` is the
    version the client last saw (409 when it changed meanwhile); omit it to create."""

    model_config = ConfigDict(extra="forbid")

    strategies: tuple[StrategyKey, ...] = Field(min_length=1)
    watchlist_id: str | None = None
    morning_list: bool = True
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=MAX_DAILY_LIMIT)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=0)


def _subscriptions(session: AsyncSession = Depends(get_session)) -> StrategySubscriptionStore:
    return StrategySubscriptionStore(session)


@router.get("/strategy-watch/subscription")
async def get_strategy_subscription(
    user: User = Depends(require_user),
    subscriptions: StrategySubscriptionStore = Depends(_subscriptions),
) -> StrategySubscription | None:
    return await subscriptions.get(user_id=user.id)


@router.put("/strategy-watch/subscription")
async def put_strategy_subscription(
    body: PutStrategySubscriptionRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
    subscriptions: StrategySubscriptionStore = Depends(_subscriptions),
) -> StrategySubscription:
    if body.watchlist_id is not None and not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=body.watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {body.watchlist_id} not found.")
    # Keep the order the strategies are always shown in, and drop repeats.
    strategies = tuple(dict.fromkeys(body.strategies))
    try:
        return await subscriptions.put(
            user_id=user.id,
            strategies=strategies,
            watchlist_id=body.watchlist_id,
            morning_list=body.morning_list,
            channels=body.channels,
            quiet_hours=body.quiet_hours,
            daily_limit=body.daily_limit,
            enabled=body.enabled,
            expected_version=body.expected_version,
        )
    except StaleSubscriptionError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="stale_version",
            message="The subscription changed on another device. Reload and try again.",
            retryable=False,
        ) from exc


@router.delete("/strategy-watch/subscription", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy_subscription(
    user: User = Depends(require_user),
    subscriptions: StrategySubscriptionStore = Depends(_subscriptions),
) -> None:
    if not await subscriptions.delete(user_id=user.id):
        raise _not_found("subscription_not_found", "No strategy-watch subscription to delete.")


__all__ = ["router"]
