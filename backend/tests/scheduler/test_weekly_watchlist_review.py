"""The weekly watchlist review as a schedule: its catalogue entry and trigger, validation, and the
worker's handling (a normal run, an empty watchlist that is not an error, quota and BYOK exactly as
the other skill-backed schedules)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ingest.leader import AlwaysLeader
from scheduler.models import (
    DeliveryChannelKind,
    JobStatus,
    NotificationSource,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.quota import AllowAllQuotaGate, DenyAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.triggers import next_run_after
from scheduler.validation import InvalidTriggerError, validate_action, validate_trigger
from scheduler.worker import SchedulerWorker
from schemas.enums import Market, Tier
from schemas.schedule_templates import (
    SCHEDULE_TEMPLATES,
    WEEKLY_REVIEW_DEFAULT_CRON,
    ScheduleTemplateId,
    build_inputs,
    is_weekly_review,
    weekly_review_cron,
)
from skills.registry import SkillRegistry
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_worker import (
    _CLEAN_ANSWER,
    _ctx,
    _InputRecordingOrchestrator,
    _seed_watchlist,
    _StubOrchestrator,
    _StubRouter,
)

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"
TEMPLATE = SCHEDULE_TEMPLATES[ScheduleTemplateId.WEEKLY_WATCHLIST_REVIEW]


# ---- the catalogue entry ------------------------------------------------------------------------


def test_the_template_runs_the_watchlist_review_skill_weekly_in_taipei() -> None:
    assert TEMPLATE.skill_ref == "watchlist-review"
    assert TEMPLATE.trigger_type == "cron"
    assert TEMPLATE.trigger_spec == WEEKLY_REVIEW_DEFAULT_CRON == "0 9 * * 6"
    assert TEMPLATE.market is Market.TW
    assert TEMPLATE.takes_targets is False
    assert build_inputs(TEMPLATE, []) == {"review": "weekly"}
    assert build_inputs(TEMPLATE, ["2330"]) == {"review": "weekly"}  # tickers are never an input


def test_the_default_fires_saturday_morning_taipei() -> None:
    friday_noon = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)
    fire = next_run_after(
        ScheduleTrigger(type=TriggerType.CRON, spec=WEEKLY_REVIEW_DEFAULT_CRON, market=Market.TW),
        friday_noon,
    )
    assert fire == datetime(2026, 10, 24, 1, 0, tzinfo=UTC)  # Saturday 09:00 in Taipei
    assert fire.weekday() == 5


@pytest.mark.parametrize(
    ("weekday", "hour", "minute", "spec"),
    [(None, 9, 0, "0 9 * * 6"), ("sun", 20, 30, "30 20 * * 0"), ("mon", 7, 5, "5 7 * * 1")],
)
def test_user_chosen_day_and_time_become_a_concrete_cron(
    weekday: str | None, hour: int, minute: int, spec: str
) -> None:
    assert weekly_review_cron(weekday, hour, minute) == spec
    validate_trigger(ScheduleTrigger(type=TriggerType.CRON, spec=spec, market=Market.TW))


def test_a_wildcard_time_is_still_refused_as_too_frequent() -> None:
    with pytest.raises(InvalidTriggerError):
        validate_trigger(ScheduleTrigger(type=TriggerType.CRON, spec="* * * * 6", market=Market.TW))


def test_the_action_names_a_registered_governed_skill() -> None:
    registry = SkillRegistry()
    registry.load([_BUILT_INS])
    validate_action(
        ScheduleAction(skill_ref="watchlist-review", inputs={"review": "weekly"}), registry
    )


def test_only_the_weekly_mode_counts_as_the_review() -> None:
    assert is_weekly_review("watchlist-review", {"review": "weekly"})
    assert not is_weekly_review("watchlist-review", {})
    assert not is_weekly_review("watchlist-review", {"watchlist_name": "x"})
    assert not is_weekly_review("daily-context-brief", {"review": "weekly"})


# ---- the worker ---------------------------------------------------------------------------------


async def _review_job(session_factory, user: str):
    async with session_factory() as session:
        job = await SqlAlchemySchedulerStore(session).create_job(
            user_id=user,
            trigger=ScheduleTrigger(type=TriggerType.CRON, spec="0 9 * * 6", market=Market.TW),
            action=ScheduleAction(skill_ref="watchlist-review", inputs={"review": "weekly"}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime.now(UTC) - timedelta(minutes=1),
        )
        await session.commit()
        return job


def _worker(session_factory, orch, *, quota=None) -> SchedulerWorker:
    return SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=quota or AllowAllQuotaGate(),
        )
    )


async def test_a_review_runs_the_skill_with_its_mode_and_links_the_thread(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _seed_watchlist(session_factory, user, ["2330"])
    job = await _review_job(session_factory, user)
    orch = _InputRecordingOrchestrator()

    assert await _worker(session_factory, orch).run_due_jobs() == [job.id]

    assert orch.calls == ["watchlist-review"]
    assert "review=weekly" in orch.messages[0]
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        (note,) = await store.list_notifications(user_id=user)
        refreshed = await store.get_job(user_id=user, job_id=job.id)
    assert note.source is NotificationSource.SCHEDULE
    assert note.payload["deep_link"]["route"] == "thread"  # type: ignore[index]
    assert refreshed.status is JobStatus.OK
    assert refreshed.thread_id is not None
    assert refreshed.next_run is not None


async def test_an_empty_watchlist_gets_one_calm_line_and_is_not_an_error(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _seed_watchlist(session_factory, user, [])
    job = await _review_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)

    assert await _worker(session_factory, orch).run_due_jobs() == []

    assert orch.calls == []  # no model run, so no chat spent
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        (note,) = await store.list_notifications(user_id=user)
        refreshed = await store.get_job(user_id=user, job_id=job.id)
    assert note.payload["body"] == "本週沒有可回顧的自選股：你的自選股群組目前是空的。"
    assert note.payload["title"] == "自選股週回顧"
    assert refreshed.status is JobStatus.OK
    assert refreshed.status_reason == "nothing_to_review"
    assert refreshed.enabled is True
    assert refreshed.next_run is not None
    assert refreshed.next_run.replace(tzinfo=UTC) > datetime.now(UTC)


async def test_a_user_with_no_watchlist_at_all_is_treated_the_same(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    job = await _review_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    await _worker(session_factory, orch).run_due_jobs()
    async with session_factory() as session:
        refreshed = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
    assert (refreshed.status, refreshed.status_reason) == (JobStatus.OK, "nothing_to_review")


async def test_an_empty_watchlist_does_not_reserve_a_chat(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    job = await _review_job(session_factory, user)
    # A gate that refuses everything would mark the job skipped_quota if it were ever asked.
    await _worker(
        session_factory, _StubOrchestrator(_CLEAN_ANSWER), quota=DenyAllQuotaGate()
    ).run_due_jobs()
    async with session_factory() as session:
        refreshed = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
    assert refreshed.status is JobStatus.OK


async def test_the_review_counts_against_the_chat_quota_like_any_scheduled_run(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _seed_watchlist(session_factory, user, ["2330"])
    job = await _review_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)

    assert await _worker(session_factory, orch, quota=DenyAllQuotaGate()).run_due_jobs() == []

    assert orch.calls == []
    async with session_factory() as session:
        refreshed = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
    assert refreshed.status is JobStatus.SKIPPED_QUOTA
    assert refreshed.status_reason == "chat_quota_exhausted"


async def test_a_second_run_the_same_day_does_not_notify_twice(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _review_job(session_factory, user)
    worker = _worker(session_factory, _StubOrchestrator(_CLEAN_ANSWER))
    await worker.run_due_jobs()
    async with session_factory() as session:
        job = (await SqlAlchemySchedulerStore(session).list_jobs(user_id=user))[0]
        await SqlAlchemySchedulerStore(session).set_job_status(
            job_id=job.id,
            status=JobStatus.OK,
            next_run=datetime.now(UTC) - timedelta(minutes=1),
        )
        await session.commit()
    await worker.run_due_jobs()
    async with session_factory() as session:
        notes = await SqlAlchemySchedulerStore(session).list_notifications(user_id=user)
    assert len(notes) == 1  # the deliveries dedupe key holds


async def test_a_byok_owner_runs_the_review_on_their_own_key_and_skips_the_chat_cap(
    session_factory,
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from tests.scheduler.test_worker import (  # noqa: PLC0415
        _byok_settings,
        _KeyProbeOrchestrator,
        _save_key,
    )

    settings = _byok_settings()
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _save_key(session_factory, settings, user, "anthropic")
    await _seed_watchlist(session_factory, user, ["2330"])
    await _review_job(session_factory, user)
    orch = _KeyProbeOrchestrator(_CLEAN_ANSWER)
    ctx = _ctx(
        session_factory,
        orchestrator=orch,
        router=_StubRouter({}),
        leader=AlwaysLeader(),
        quota_gate=DenyAllQuotaGate(),  # would refuse a platform-key run
    )
    assert len(await SchedulerWorker(replace(ctx, settings=settings)).run_due_jobs()) == 1
    (seen,) = orch.seen
    assert seen is not None
    assert seen.provider_id == "anthropic"  # type: ignore[attr-defined]
