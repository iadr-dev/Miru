"""The pet's two touch points on a chat turn: its tone hint going in, its reward coming out.

Kept apart from `turn_driver` so the cosmetic-only boundary is easy to see: the hint is a string
for the styling layer, and the reward only moves pet counters and emits `pet.*` events. Nothing
here reads entitlements, quota or data, and a failure here never fails an answered turn.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from events.bus import EventBus
from pets import pet_hint_for
from pets.store import SqlAlchemyPetStore

logger = structlog.get_logger(__name__)


async def pet_hint_for_user(session: AsyncSession, *, user_id: str) -> str | None:
    """The equipped pet's tone hint, or None unless the user turned pet-flavoured replies on."""
    try:
        store = SqlAlchemyPetStore(session)
        stats = await store.ensure_stats(user_id=user_id)
        if not stats.hint_enabled:
            return None
        return pet_hint_for(await store.active_pet(user_id=user_id))
    except Exception as exc:  # cosmetic: a pet problem must not block the answer
        logger.warning("pet_hint_failed", user_id=user_id, error=repr(exc))
        return None


async def reward_completed_chat(
    session: AsyncSession, *, event_bus: EventBus, turn_id: str, user_id: str
) -> None:
    """Grant the equipped pet its chat XP and announce a level-up or a newly earned pull."""
    try:
        reward = await SqlAlchemyPetStore(session).grant_chat_xp(user_id=user_id)
        await session.commit()
    except Exception as exc:  # best-effort, like the usage write
        await session.rollback()
        logger.warning("pet_reward_failed", turn_id=turn_id, error=repr(exc))
        return
    if reward.leveled_to is not None and reward.pet_id is not None:
        event_bus.publish(
            "pet.leveled",
            turn_id=turn_id,
            data={"pet_id": reward.pet_id, "level": reward.leveled_to},
        )
    if reward.pulls_earned > 0:
        event_bus.publish("pet.pull_earned", turn_id=turn_id, data={"count": reward.pulls_earned})
