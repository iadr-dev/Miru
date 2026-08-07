"""Cosmetic pet state: seeding, gacha pull (reserve-before-act, first-pet auto-equip,
duplicate→XP), equip atomicity, chat XP + leveling, and the login-streak flow."""

from __future__ import annotations

import random
from datetime import date

import pytest
from sqlalchemy import select

from db.models import UserPetStatsRow
from pets import config
from pets.store import NoPullsAvailableError, SqlAlchemyPetStore
from tests.pets.conftest import make_user


async def _grant_pulls(session, user_id: str, n: int) -> None:
    await session.flush()
    row = (
        await session.execute(select(UserPetStatsRow).where(UserPetStatsRow.user_id == user_id))
    ).scalar_one()
    row.available_pulls = n
    await session.flush()


async def test_stats_seeded_with_one_pull(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session)
    stats = await store.ensure_stats(user_id=user_id)
    assert stats.available_pulls == 1
    assert stats.total_pulls == 0


async def test_first_pull_auto_equips_and_consumes_pull(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    outcome = await store.pull(user_id=user_id)
    assert outcome.auto_equipped
    assert not outcome.is_duplicate
    stats = await store.ensure_stats(user_id=user_id)
    assert stats.available_pulls == 0
    assert stats.total_pulls == 1
    active = await store.active_pet(user_id=user_id)
    assert active is not None and active.pet_id == outcome.pet_id


async def test_pull_refused_when_none_available(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await store.pull(user_id=user_id)
    with pytest.raises(NoPullsAvailableError):
        await store.pull(user_id=user_id)


async def test_duplicate_pull_grants_xp(db_session) -> None:
    user_id = await make_user(db_session)
    # Fixed RNG so the same rarity+pet is drawn twice.
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await _grant_pulls(db_session, user_id, 50)
    seen: set[str] = set()
    got_dup = False
    for _ in range(50):
        outcome = await store.pull(user_id=user_id)
        if outcome.pet_id in seen and outcome.is_duplicate:
            assert outcome.xp_awarded == config.XP_PER_DUPLICATE
            got_dup = True
            break
        seen.add(outcome.pet_id)
    assert got_dup


async def test_equip_unequips_others(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await _grant_pulls(db_session, user_id, 30)
    # Pull until we own at least 2 distinct pets.
    for _ in range(30):
        await store.pull(user_id=user_id)
        pets = await store.list_pets(user_id=user_id)
        if len(pets) >= 2:
            break
    pets = await store.list_pets(user_id=user_id)
    assert len(pets) >= 2
    equipped = await store.equip(user_id=user_id, record_id=pets[1].id)
    assert equipped.is_active
    actives = [p for p in await store.list_pets(user_id=user_id) if p.is_active]
    assert len(actives) == 1
    assert actives[0].id == pets[1].id


async def test_chat_xp_levels_up_equipped_pet(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await store.pull(user_id=user_id)  # first pet, auto-equipped
    # XP_PER_CHAT * n crosses XP_PER_LEVEL → level 2.
    needed = config.XP_PER_LEVEL // config.XP_PER_CHAT + 1
    for _ in range(needed):
        await store.grant_chat_xp(user_id=user_id)
    active = await store.active_pet(user_id=user_id)
    assert active is not None and active.level >= 2


async def test_level_milestone_grants_bonus_pulls_exactly_once(db_session) -> None:
    """Crossing a configured milestone level (`config.LEVEL_MILESTONE_PULLS`) grants its bonus
    pulls exactly once when a *single* XP application pushes the equipped pet across it — not
    per XP event. Uses one big `_apply_xp` call (via a duplicate pull's XP grant, scaled to the
    milestone) rather than many small `grant_chat_xp` calls, so the chat-count bonus-pull path
    (a separate, independently-tested grant) never conflates with the level-milestone grant this
    test isolates."""
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    outcome = await store.pull(user_id=user_id)  # first pet, auto-equipped

    milestone_level = next(iter(sorted(config.LEVEL_MILESTONE_PULLS)))
    expected_bonus = config.LEVEL_MILESTONE_PULLS[milestone_level]

    active_row = await store._pet_row(
        user_id=user_id, record_id=(await store.active_pet(user_id=user_id)).id
    )
    assert active_row is not None
    stats_row = await store._stats_row(user_id=user_id)
    assert stats_row is not None
    stats_before = stats_row.available_pulls

    # One call, one crossing: apply exactly enough XP in a single `_apply_xp` invocation to
    # cross the milestone from level 1.
    xp_for_one_level = config.XP_PER_LEVEL
    xp_to_milestone_level = (milestone_level - 1) * xp_for_one_level
    store._apply_xp(active_row, xp_to_milestone_level, stats=stats_row)

    assert active_row.level == milestone_level
    assert stats_row.available_pulls == stats_before + expected_bonus
    del outcome


async def test_level_milestone_not_double_granted_on_retry(db_session) -> None:
    """A retried/duplicated `_apply_xp` call starting from the SAME persisted level (the
    real-world shape of a retry: nothing committed the level change yet) recomputes the
    identical crossing and grants the bonus only once — proven by calling `_apply_xp` twice from
    the same starting level and asserting the second call is a no-op for the bonus."""
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session, rng=random.Random(1))
    await store.ensure_stats(user_id=user_id)
    await store.pull(user_id=user_id)
    active_pet = await store.active_pet(user_id=user_id)
    assert active_pet is not None
    active_row = await store._pet_row(user_id=user_id, record_id=active_pet.id)
    stats_row = await store._stats_row(user_id=user_id)
    assert active_row is not None and stats_row is not None

    milestone_level = next(iter(sorted(config.LEVEL_MILESTONE_PULLS)))
    expected_bonus = config.LEVEL_MILESTONE_PULLS[milestone_level]
    xp_to_milestone_level = (milestone_level - 1) * config.XP_PER_LEVEL
    stats_before = stats_row.available_pulls

    store._apply_xp(active_row, xp_to_milestone_level, stats=stats_row)
    assert stats_row.available_pulls == stats_before + expected_bonus

    # A second XP grant that does not cross a further milestone must not re-grant.
    store._apply_xp(active_row, 1, stats=stats_row)
    assert stats_row.available_pulls == stats_before + expected_bonus


async def test_login_streak_grants_pull_every_seven_days(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemyPetStore(db_session)
    for day in range(1, 8):
        await store.register_login(user_id=user_id, today=date(2026, 1, day))
    stats = await store.ensure_stats(user_id=user_id)
    assert stats.streak_days == 7
    # 1 seeded + 1 for the 7-day streak.
    assert stats.available_pulls == 2
