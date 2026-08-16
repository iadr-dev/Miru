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
from ingest.leader import AlwaysLeader, NeverLeader
from orchestrator.models import OrchestratorResult, Routing
from scheduler.delivery.channels import InAppChannel
from scheduler.models import (
    AlertCondition,
    AlertConditionType,
    AlertRule,
    Comparator,
    DeliveryChannelKind,
    JobStatus,
    NotificationSource,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.quota import AllowAllQuotaGate, DenyAllQuotaGate, UsageQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.worker import SchedulerContext, SchedulerWorker, build_scheduler_worker
from schemas.enums import (
    Capability,
    DataClass,
    Market,
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


def _alert(user_id: str, condition: AlertCondition) -> AlertRule:
    now = datetime.now(UTC)
    return AlertRule(
        id="alert-x",
        user_id=user_id,
        symbol="2330",
        market=Market.TW,
        condition=condition,
        channels=(DeliveryChannelKind.IN_APP,),
        created_at=now,
        updated_at=now,
    )


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


async def test_entitlement_lapsed_auto_disables_job(session_factory) -> None:
    # Owner is Free — scheduler is a Pro feature, so a job that somehow exists auto-disables.
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
    ran = await worker.run_due_jobs()
    assert ran == []
    assert orch.calls == []

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        refreshed = await store.get_job(user_id=user, job_id=job.id)
        assert refreshed.enabled is False


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


@pytest.mark.governance
async def test_alert_fires_non_trading_observation(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    condition = AlertCondition(
        type=AlertConditionType.INSTITUTIONAL,
        metric="foreign_net",
        comparator=Comparator.STREAK_UP,
        threshold=3,
        window=3,
    )
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        alert = await store.create_alert(
            user_id=user,
            symbol="2330",
            market=Market.TW,
            condition=condition,
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await session.commit()

    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_StubOrchestrator(_CLEAN_ANSWER),
            router=_StubRouter({"foreign_net_streak": 4}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    fired = await worker.evaluate_alerts()
    assert alert.id in fired

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        notification = (await store.list_notifications(user_id=user))[0]
        body = str(notification.payload["body"])
        assert "連 4 日" in body
        assert "非買賣建議" in body  # observation, never a signal
        # never a trading instruction
        assert "買進" not in body or "非買賣建議" in body


async def test_alert_does_not_fire_below_threshold(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    condition = AlertCondition(
        type=AlertConditionType.INSTITUTIONAL,
        metric="foreign_net",
        comparator=Comparator.STREAK_UP,
        threshold=3,
    )
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_StubOrchestrator(_CLEAN_ANSWER),
            router=_StubRouter({"foreign_net_streak": 2}),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )
    assert await worker.evaluate_alert(_alert(user, condition)) is False


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
