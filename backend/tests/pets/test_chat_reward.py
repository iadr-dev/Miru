"""What a completed chat earns the equipped pet: capped daily XP, level-up reporting, and the
opt-in tone hint."""

from __future__ import annotations

import random

from pets import config
from pets.persona import pet_hint_for
from pets.store import SqlAlchemyPetStore
from tests.pets.conftest import make_user


async def test_a_chat_earns_xp_up_to_the_daily_cap(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await store.pull(user_id=user_id)
    rewarded = 0
    for _ in range(config.DAILY_CHAT_XP_CAP // config.XP_PER_CHAT + 5):
        reward = await store.grant_chat_xp(user_id=user_id)
        rewarded += reward.xp
    assert rewarded == config.DAILY_CHAT_XP_CAP
    stats = await store.ensure_stats(user_id=user_id)
    assert stats.chat_count == config.DAILY_CHAT_XP_CAP // config.XP_PER_CHAT
    assert (await store.grant_chat_xp(user_id=user_id)).xp == 0


async def test_the_reward_reports_a_level_up(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await store.pull(user_id=user_id)
    rewards = [
        await store.grant_chat_xp(user_id=user_id)
        for _ in range(config.XP_PER_LEVEL // config.XP_PER_CHAT)
    ]
    assert [r.leveled_to for r in rewards[:-1]] == [None] * (len(rewards) - 1)
    assert rewards[-1].leveled_to == 2
    assert rewards[-1].pet_id is not None


async def test_a_chat_without_a_pet_earns_nothing_for_a_pet(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session)
    reward = await store.grant_chat_xp(user_id=user_id)
    assert reward.pet_id is None and reward.leveled_to is None


async def test_the_tone_hint_is_off_until_the_user_opts_in(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    assert (await store.ensure_stats(user_id=user_id)).hint_enabled is False
    await store.pull(user_id=user_id)
    assert (await store.set_hint_enabled(user_id=user_id, enabled=True)).hint_enabled is True
    assert pet_hint_for(await store.active_pet(user_id=user_id)) is not None
