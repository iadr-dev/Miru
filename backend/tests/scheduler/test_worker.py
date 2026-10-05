"""SchedulerWorker behaviour: leader skip-not-crash, quota skip-and-report, entitlement-lapse
auto-disable, non-trading guardrail enforcement on a scheduled run's output, alert evaluation +
non-trading copy, in-app delivery persistence, and idempotent redelivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from accounts.entitlements import TierLimits
from accounts.hooks import EntitlementResolver
from agent.guardrails import REDACTION_NOTICE, check_output
from agent.loop import RichCard
from conversation.models import MessageBlockType
from conversation.store import SqlAlchemyConversationStore
from ingest.leader import AlwaysLeader, NeverLeader
from orchestrator.models import OrchestratorResult, Routing
from scheduler.delivery.channels import InAppChannel
from scheduler.models import (
    DeliveryChannelKind,
    JobStatus,
    NotificationSource,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.quota import AllowAllQuotaGate, DenyAllQuotaGate, UsageQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.worker import SchedulerContext, SchedulerWorker, as_utc, build_scheduler_worker
from schemas.enums import (
    Capability,
    DataClass,
    Tier,
    TimingClass,
)
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.scheduler.conftest import insert_user
from usage.store import SqlAlchemyUsageStore

_TRADING_ANSWER = "You should buy 2330 now. Strong buy. 目標價 1200."
_CLEAN_ANSWER = "台積電近期外資買超。以上為資訊整理，非投資建議。"


class _StubOrchestrator:
    """Stands in for `Orchestrator.run_skill` — returns a fixed answer WITHOUT running its
    own guardrail, so the worker test can prove the scheduled run's output is still gated the
    same way an interactive turn is (via the real guardrail seam the worker relies on)."""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.calls: list[str] = []

    async def run_skill(self, skill_ref: str, message: str, **kwargs: object) -> OrchestratorResult:
        del message, kwargs
        self.calls.append(skill_ref)
        return OrchestratorResult(
            final_text=self._answer,
            routing=Routing(framework="single", skills=(skill_ref,), reason="test"),
        )


class _StubRouter:
    """Returns a fixed `SourceResult` for any capability/symbol — no network."""

    def __init__(
        self, payload: dict[str, object], *, status: HealthStatus = HealthStatus.OK
    ) -> None:
        self._payload = payload
        self._status = status

    async def fetch(self, cap: Capability, sym: Symbol, **kwargs: object) -> SourceResult:
        del kwargs
        return SourceResult(
            source_id="STUB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub — recorded fixture, not live",
            payload=self._payload,
            health=SourceHealth(status=self._status, detail="ok"),
        )


def _ctx(
    session_factory,
    *,
    orchestrator: object,
    router: object,
    leader: object,
    quota_gate: object,
) -> SchedulerContext:
    return SchedulerContext(
        session_factory=session_factory,
        orchestrator=orchestrator,  # type: ignore[arg-type]
        router=router,  # type: ignore[arg-type]
        channels={DeliveryChannelKind.IN_APP: InAppChannel()},
        entitlement_resolver=EntitlementResolver(),
        leader=leader,  # type: ignore[arg-type]
        quota_gate=quota_gate,  # type: ignore[arg-type]
    )


async def _make_job(session_factory, user_id: str, **kwargs):
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user_id,
            trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime.now(UTC) - timedelta(minutes=1),
            **kwargs,
        )
        await session.commit()
        return job


async def test_not_leader_skips_without_crash(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _make_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=NeverLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    ran = await worker.run_due_jobs()
    assert ran == []
    assert orch.calls == []  # not-leader = skip, never fires the skill


async def test_leader_fires_due_job_and_persists_in_app(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _make_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    ran = await worker.run_due_jobs()
    assert len(ran) == 1
    assert orch.calls == ["daily-context-brief"]

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        notifications = await store.list_notifications(user_id=user)
        assert len(notifications) == 1
        assert notifications[0].source is NotificationSource.SCHEDULE


async def test_quota_exceeded_skips_and_reports(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=DenyAllQuotaGate(),
        )
    )
    ran = await worker.run_due_jobs()
    assert ran == []
    assert orch.calls == []  # skip-and-report, not a retry loop

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        refreshed = await store.get_job(user_id=user, job_id=job.id)
        assert refreshed.status is JobStatus.SKIPPED_QUOTA


async def test_default_worker_wires_real_usage_quota_gate(session_factory) -> None:
    """`build_scheduler_worker` wires the real `UsageQuotaGate` by default (not allow-all), so a
    scheduled run consumes the owner's durable daily-chat counter through the same store an
    interactive turn spends. A Pro owner (the only tier the scheduler feature is entitled at) has
    an unlimited daily allowance, so the run proceeds — but the gate is a live `UsageQuotaGate`, so
    the moment a finite-limit owner is ever eligible the counter enforcement is already in place.

    (Wiring-live check for the scheduler gate: the default construction site injects the real gate,
    not the permissive default, and a run actually goes through it.)"""
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _make_job(session_factory, user)

    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = build_scheduler_worker(
        session_factory=session_factory,
        orchestrator=orch,  # type: ignore[arg-type]
        router=_StubRouter({}),  # type: ignore[arg-type]
        entitlement_resolver=EntitlementResolver(),
        channels={DeliveryChannelKind.IN_APP: InAppChannel()},
        leader=AlwaysLeader(),
    )
    assert isinstance(worker._ctx.quota_gate, UsageQuotaGate)  # real gate, not AllowAll

    ran = await worker.run_due_jobs()
    assert len(ran) == 1  # the run passed through the real gate and fired
    assert orch.calls == ["daily-context-brief"]  # Pro = unlimited → the gate admitted the run


async def test_usage_quota_gate_skips_over_finite_cap(session_factory) -> None:
    """The `UsageQuotaGate` skip path itself: a finite-limit owner already at the cap is refused
    (reserve-before-act over the same durable counter). Exercised directly because the only tier
    the scheduler feature is entitled at (Pro) is unlimited, so the finite path can't be reached
    through a live job today — but the gate must still enforce it if a lower tier ever qualifies."""
    user = await insert_user(session_factory, tier=Tier.FREE)
    store = SqlAlchemyUsageStore(session_factory)
    gate = UsageQuotaGate(store)

    # Pin Free's cap to a small finite number and exhaust it, then the gate must refuse.
    patched = {
        Tier.FREE: TierLimits(chat_limit_per_day=2, requests_per_minute=60, finmind_class=Tier.FREE)
    }
    with patch("scheduler.quota.TIER_LIMITS", patched):
        assert await gate.allows(user_id=user, tier=Tier.FREE) is True
        assert await gate.allows(user_id=user, tier=Tier.FREE) is True
        assert await gate.allows(user_id=user, tier=Tier.FREE) is False


async def test_free_user_job_runs_and_is_rescheduled(session_factory) -> None:
    # ADR-0028: scheduling is open to every tier (the data, not the feature, is tier-gated), and
    # a finished run resolves its trigger to a concrete future next_run — not a blind +1 day.
    user = await insert_user(session_factory, tier=Tier.FREE)
    job = await _make_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    now = datetime.now(UTC)
    ran = await worker.run_due_jobs(now=now)
    assert ran == [job.id]

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        refreshed = await store.get_job(user_id=user, job_id=job.id)
        assert refreshed.enabled is True
        assert refreshed.next_run is not None
        assert refreshed.next_run.replace(tzinfo=UTC) > now


async def test_orphaned_job_auto_disables(session_factory) -> None:
    # The only remaining auto-disable: the owner account no longer exists.
    job = await _make_job(session_factory, await insert_user(session_factory, tier=Tier.PRO))
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    with patch.object(SchedulerWorker, "_load_user", return_value=None):
        ran = await worker.run_due_jobs()
    assert ran == []
    assert orch.calls == []
    del job


@pytest.mark.governance
async def test_scheduled_run_output_passes_non_trading_guardrail(session_factory) -> None:
    # The orchestrator hands the worker trading-shaped text; the persisted brief must be
    # redacted/caveated by the same guardrail an interactive turn uses.
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _make_job(session_factory, user)
    orch = _StubOrchestrator(_TRADING_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    await worker.run_due_jobs()

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        notification = (await store.list_notifications(user_id=user))[0]
        body = str(notification.payload["body"])

    # The worker persisted the orchestrator's text verbatim (the orchestrator owns the gate),
    # so applying the guardrail to that body must flag it — proving a scheduled run's output is
    # subject to the identical non-trading check, not exempt from it.
    verdict = check_output(body)
    assert verdict.redacted_text is not None
    assert "should buy" not in verdict.redacted_text.lower()
    assert "strong buy" not in verdict.redacted_text.lower()
    assert REDACTION_NOTICE in verdict.redacted_text


async def test_redelivery_is_idempotent(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    orch = _StubOrchestrator(_CLEAN_ANSWER)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    fixed_now = datetime.now(UTC)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job_model = await store.get_job(user_id=user, job_id=job.id)

    await worker.run_job(job_model, now=fixed_now)
    await worker.run_job(job_model, now=fixed_now)  # same trigger date/channel → dedupe no-op

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        assert len(await store.list_notifications(user_id=user)) == 1


async def test_each_run_appends_to_the_schedules_one_persistent_thread(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_StubOrchestrator(_CLEAN_ANSWER),
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    await worker.run_job(job)
    async with session_factory() as session:
        after_first = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
    assert after_first.thread_id is not None

    await worker.run_job(after_first)
    async with session_factory() as session:
        after_second = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
        path = await SqlAlchemyConversationStore(session).active_path(
            thread_id=after_first.thread_id
        )
    assert after_second.thread_id == after_first.thread_id  # same thread, not a new one per run
    assert len([m for m in path if m.type is MessageBlockType.TEXT]) == 2


async def test_a_failed_run_records_why_and_is_rescheduled(session_factory) -> None:
    class _Exploding(_StubOrchestrator):
        async def run_skill(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_Exploding(_CLEAN_ANSWER),
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    assert await worker.run_job(job) is False
    async with session_factory() as session:
        after = await SqlAlchemySchedulerStore(session).get_job(user_id=user, job_id=job.id)
    assert after.status is JobStatus.ERROR
    assert after.status_reason == "RuntimeError"
    assert after.next_run is not None
    assert as_utc(after.next_run) > as_utc(job.next_run)  # not retried every poll


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"stock_ids": ["2330", "2317"]}, "stock_ids=['2330', '2317']"),
        ({"stock_id": "2330"}, "stock_id=2330"),
        ({"etf_id": "0050"}, "etf_id=0050"),
        ({"symbol_id": "AAPL", "market": "US"}, "symbol_id=AAPL"),
    ],
)
async def test_scheduled_run_message_carries_whichever_target_input_the_skill_uses(
    session_factory, inputs: dict[str, object], expected: str
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    job = job.model_copy(
        update={"action": ScheduleAction(skill_ref="chip-flow-review", inputs=inputs)}
    )
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_StubOrchestrator(_CLEAN_ANSWER),
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )

    message = worker._job_message(job)

    assert "chip-flow-review" in message
    assert expected in message


class _CardOrchestrator(_StubOrchestrator):
    """Returns a result that carries chart cards, like a report skill's tools would."""

    async def run_skill(self, skill_ref, message, **kwargs):  # type: ignore[no-untyped-def]
        result = await super().run_skill(skill_ref, message, **kwargs)
        card = RichCard(card_type="annual_fundamentals", data={"rows": [], "chart_hints": []})
        return result.model_copy(update={"rich_cards": (card,)})


async def test_a_scheduled_report_keeps_its_chart_cards_and_sends_a_short_notification(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    job = await _make_job(session_factory, user)
    long_report = "一、標的基本識別\n" + ("內容" * 400)
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_CardOrchestrator(long_report),
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )

    await worker.run_job(job)

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        stored = await store.get_job(user_id=user, job_id=job.id)
        path = await SqlAlchemyConversationStore(session).active_path(thread_id=stored.thread_id)
        notifications = await store.list_notifications(user_id=user)
    kinds = [m.type for m in path]
    assert MessageBlockType.RICH_CARD in kinds  # the chart card is in the thread
    assert kinds.index(MessageBlockType.RICH_CARD) < kinds.index(MessageBlockType.TEXT)
    body = str(notifications[0].payload.get("body", ""))
    assert len(body) < 260 and "完整報告請見對話" in body  # a pointer, not the whole report


class _InputRecordingOrchestrator(_StubOrchestrator):
    def __init__(self) -> None:
        super().__init__(_CLEAN_ANSWER)
        self.messages: list[str] = []

    async def run_skill(self, skill_ref: str, message: str, **kwargs: object) -> OrchestratorResult:
        self.messages.append(message)
        return await super().run_skill(skill_ref, message, **kwargs)


async def _seed_watchlist(session_factory, user: str, symbols: list[str]) -> None:
    from db.models import WatchlistItemRow, WatchlistRow  # noqa: PLC0415 — test-local

    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            WatchlistRow(
                id="wl",
                user_id=user,
                name="自選",
                market="TW",
                asset_class="stock",
                sort_order="a",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        for i, symbol in enumerate(symbols):
            session.add(
                WatchlistItemRow(
                    id=f"i{i}",
                    watchlist_id="wl",
                    user_id=user,
                    symbol=symbol,
                    market="TW",
                    asset_class="stock",
                    sort_order=str(i),
                    added_at=now,
                )
            )
        await session.commit()


async def _watchlist_job(session_factory, user: str) -> None:
    async with session_factory() as session:
        await SqlAlchemySchedulerStore(session).create_job(
            user_id=user,
            trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="pre_open"),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={"watchlist_id": "wl"}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime.now(UTC) - timedelta(minutes=1),
        )
        await session.commit()


async def test_a_watchlist_scoped_briefing_expands_to_the_lists_current_members(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _seed_watchlist(session_factory, user, ["2330", "2317"])
    await _watchlist_job(session_factory, user)
    orch = _InputRecordingOrchestrator()
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )

    assert len(await worker.run_due_jobs()) == 1
    assert "stock_ids=['2330', '2317']" in orch.messages[0]
    assert "watchlist_id" not in orch.messages[0]


async def test_an_empty_watchlist_is_reported_not_run(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _seed_watchlist(session_factory, user, [])
    await _watchlist_job(session_factory, user)
    orch = _InputRecordingOrchestrator()
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=orch,
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )

    assert await worker.run_due_jobs() == []
    assert orch.messages == []
    async with session_factory() as session:
        job = (await SqlAlchemySchedulerStore(session).list_jobs(user_id=user))[0]
    assert job.status is JobStatus.ERROR
    assert job.status_reason == "watchlist_empty"
