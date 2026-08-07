"""The scheduler worker: the job-firing / alert-evaluation loop.

A scheduled run is just an agent turn with no live human. This worker enforces, in code, every
bound the scheduling amendment is permitted under:

1. **Leader-only.** `LeaderElection.is_leader()` is checked before anything fires. Not being the
   leader is a silent skip, never an error — the same contract the ingest write path uses, so a
   real cross-process election swaps in at the construction site alone.
2. **User-authorized.** Only jobs/alerts a signed-in user created are in the store; the worker
   never invents work.
3. **Entitlement-gated at run time.** The owner's tier is resolved *fresh* each run. If the
   scheduler feature entitlement has lapsed (owner no longer Pro), the job auto-disables. Sponsor
   data inside a run is masked by the same tool-dispatch gate an interactive turn uses, because
   the run's `ToolContext` carries the owner's current data-licensing tier — a lapsed sponsor
   entitlement masks that section of the brief without spamming an upgrade prompt.
4. **Quota-bounded.** Over-quota runs are skipped and reported (`skipped_quota`), never retried
   in a tight loop. A job's own `daily_limit` is enforced independently against delivered-today
   counts.
5. **Non-trading.** The run's output is produced by the orchestrator, whose lane output passes
   `guardrails.check_output` — the same gate as an interactive turn. This worker adds no path
   that bypasses it.
6. **Read-only & coarse.** Runs go through the capability/tool/orchestrator path, never a direct
   source import and never a live stream; triggers are validated coarse at creation time.
7. **Auditable.** Every run/evaluation publishes an event carrying its owner on the shared event
   bus — the same journal/event seam interactive turns use, not a parallel audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.entitlements import feature_allowed
from accounts.hooks import EntitlementResolver
from accounts.models import User
from accounts.store import SqlAlchemyUserStore, UserNotFoundError
from conversation.models import CaveatBlock, MessageBlockType, TextBlock, TurnRole
from conversation.store import SqlAlchemyConversationStore
from dataaccess.router import DataAccessRouter
from events.bus import EventBus
from ingest.leader import LeaderElection, get_default_leader_election
from orchestrator.orchestrator import Orchestrator, PersonalityContext
from scheduler.alerts import AlertEvaluation, evaluate_condition
from scheduler.delivery.base import (
    DeliveryChannel,
    DeliveryNotification,
    DeliveryReceipt,
    DeliveryRecipient,
)
from scheduler.models import (
    AlertRule,
    DeliveryChannelKind,
    JobStatus,
    NotificationSource,
    QuietHours,
    ScheduleJob,
)
from scheduler.quota import QuotaGate, UsageQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from schemas.enums import Capability, Market
from schemas.market import Symbol
from usage.store import SqlAlchemyUsageStore

logger = structlog.get_logger(__name__)

_SCHEDULER_FEATURE = "scheduler"


def _within_quiet_hours(quiet: QuietHours | None, now: datetime) -> bool:
    if quiet is None:
        return False
    hhmm = now.strftime("%H:%M")
    if quiet.start <= quiet.end:
        return quiet.start <= hhmm < quiet.end
    # window wraps past midnight
    return hhmm >= quiet.start or hhmm < quiet.end


@dataclass(frozen=True)
class SchedulerContext:
    """Everything the worker needs, injected so a test passes stubs and prod passes the wired
    objects. `channels` maps a `DeliveryChannelKind` to its `DeliveryChannel`."""

    session_factory: async_sessionmaker[AsyncSession]
    orchestrator: Orchestrator
    router: DataAccessRouter
    channels: dict[DeliveryChannelKind, DeliveryChannel]
    entitlement_resolver: EntitlementResolver
    leader: LeaderElection
    quota_gate: QuotaGate
    event_bus: EventBus | None = None


class SchedulerWorker:
    def __init__(self, ctx: SchedulerContext) -> None:
        self._ctx = ctx

    # ---- jobs ----

    async def run_due_jobs(self, *, now: datetime | None = None) -> list[str]:
        """Fire every due job. Returns the ids of jobs that actually ran (for tests/metrics).
        A non-leader returns an empty list without touching the store — never an error."""
        if not self._ctx.leader.is_leader():
            logger.info("scheduler_skip_not_leader", phase="jobs")
            return []
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            due = await store.due_jobs(now=now)
            await session.commit()
        ran: list[str] = []
        for job in due:
            if await self.run_job(job, now=now):
                ran.append(job.id)
        return ran

    async def run_job(self, job: ScheduleJob, *, now: datetime | None = None) -> bool:
        """Run one job end to end. Returns True if the skill actually executed (vs. skipped)."""
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            user = await self._load_user(session, job.user_id)

            if user is None or not feature_allowed(user, _SCHEDULER_FEATURE):
                # Entitlement lapsed (owner no longer Pro, or account gone) — auto-disable so it
                # never fires again at this tier; no upgrade-prompt spam.
                await store.set_job_status(job_id=job.id, status=JobStatus.ERROR, disable=True)
                await session.commit()
                self._emit("schedule.disabled", user_id=job.user_id, data={"job_id": job.id})
                logger.info("schedule_job_auto_disabled", job_id=job.id, reason="entitlement")
                return False

            if _within_quiet_hours(job.quiet_hours, now):
                self._emit("schedule.skipped", user_id=user.id, data={"job_id": job.id})
                return False

            if not await self._ctx.quota_gate.allows(user_id=user.id, tier=user.tier):
                await store.set_job_status(
                    job_id=job.id, status=JobStatus.SKIPPED_QUOTA, last_run=now
                )
                await session.commit()
                self._emit("schedule.skipped_quota", user_id=user.id, data={"job_id": job.id})
                logger.info("schedule_job_skipped_quota", job_id=job.id)
                return False

            if self._daily_limit_reached(
                await store.deliveries_today(
                    user_id=user.id, source_id=job.id, trigger_date=now.date()
                ),
                job.daily_limit,
            ):
                self._emit("schedule.skipped_limit", user_id=user.id, data={"job_id": job.id})
                return False

            # Mark running so a crash mid-run is detectable as an orphaned run.
            await store.set_job_status(job_id=job.id, status=JobStatus.RUNNING)
            await session.commit()

            tool_context = self._ctx.entitlement_resolver.tool_context_for(user)
            self._emit(
                "schedule.run",
                user_id=user.id,
                data={"job_id": job.id, "skill": job.action.skill_ref},
            )
            try:
                result = await self._ctx.orchestrator.run_skill(
                    job.action.skill_ref,
                    self._job_message(job),
                    tool_context=tool_context,
                    event_bus=self._ctx.event_bus,
                    turn_id=job.id,
                    # A scheduled run has no live thread to carry a per-thread override, so the
                    # owner's own personality preference is the only resolvable level below the
                    # default — same precedence the interactive path uses, just missing the
                    # (absent) thread tier.
                    personality=PersonalityContext(user_preference=user.personality_id),
                )
            except Exception as exc:
                await store.set_job_status(job_id=job.id, status=JobStatus.ERROR, last_run=now)
                await session.commit()
                logger.warning("schedule_job_run_failed", job_id=job.id, error=str(exc))
                return False

            await self._persist_turn(session, user_id=user.id, text=result.final_text)
            await self._deliver(
                session,
                store,
                user=user,
                source=NotificationSource.SCHEDULE,
                source_id=job.id,
                channels=job.delivery_channels,
                title="排程摘要",
                body=result.final_text,
                trigger_date=now.date(),
                deep_link={"route": "thread"},
            )
            await store.set_job_status(
                job_id=job.id, status=JobStatus.OK, last_run=now, next_run=self._next_run(job, now)
            )
            await session.commit()
            return True

    # ---- alerts ----

    async def evaluate_alerts(self, *, now: datetime | None = None) -> list[str]:
        """Evaluate every enabled alert against official/EOD data. Returns fired alert ids."""
        if not self._ctx.leader.is_leader():
            logger.info("scheduler_skip_not_leader", phase="alerts")
            return []
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            alerts = await store.enabled_alerts()
            await session.commit()
        fired: list[str] = []
        for alert in alerts:
            if await self.evaluate_alert(alert, now=now):
                fired.append(alert.id)
        return fired

    async def evaluate_alert(self, alert: AlertRule, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            user = await self._load_user(session, alert.user_id)
            if user is None or not feature_allowed(user, _SCHEDULER_FEATURE):
                await store.update_alert(user_id=alert.user_id, alert_id=alert.id, enabled=False)
                await session.commit()
                return False
            if _within_quiet_hours(alert.quiet_hours, now):
                return False

            evaluation = await self._evaluate_condition(alert)
            if not evaluation.triggered:
                return False

            self._emit("alert.fired", user_id=user.id, data={"alert_id": alert.id})
            await self._deliver(
                session,
                store,
                user=user,
                source=NotificationSource.ALERT,
                source_id=alert.id,
                channels=alert.channels,
                title=f"{alert.symbol} 觀察提醒",
                body=evaluation.message,
                trigger_date=now.date(),
                deep_link={"route": "stock", "symbol": alert.symbol},
            )
            await store.mark_alert_triggered(alert_id=alert.id, when=now)
            await session.commit()
            return True

    # ---- shared internals ----

    async def _evaluate_condition(self, alert: AlertRule) -> AlertEvaluation:
        symbol = Symbol(id=alert.symbol, market=alert.market)
        cap = _capability_for(alert.market)
        result = await self._ctx.router.fetch(cap, symbol)
        return evaluate_condition(alert, result)

    async def _deliver(  # noqa: PLR0913 — one field per delivery dimension
        self,
        session: AsyncSession,
        store: SqlAlchemySchedulerStore,
        *,
        user: User,
        source: NotificationSource,
        source_id: str,
        channels: tuple[DeliveryChannelKind, ...],
        title: str,
        body: str,
        trigger_date: date,
        deep_link: dict[str, object],
    ) -> None:
        """Persist the in-app record (the dedupe guard) then route to each configured channel.
        A redelivery of the same `(source_id, trigger_date, channel)` is a no-op via the unique
        dedupe key — at-least-once execution stays idempotent."""
        selected = channels or (DeliveryChannelKind.IN_APP,)
        recipient = DeliveryRecipient(
            user_id=user.id,
            email_address=user.email,
            device_tokens=tuple(d.token for d in await store.list_devices(user_id=user.id)),
        )
        for channel_kind in selected:
            record = await store.record_notification(
                user_id=user.id,
                source=source,
                source_id=source_id,
                channel=channel_kind,
                trigger_date=trigger_date,
                payload={"title": title, "body": body, "deep_link": deep_link},
            )
            if record is None:
                continue  # dedupe no-op — already delivered on this channel today
            channel = self._ctx.channels.get(channel_kind)
            if channel is None:
                continue
            notification = DeliveryNotification(
                notification_id=record.id, title=title, body=body, deep_link=deep_link
            )
            receipt = await channel.send(notification, recipient)
            self._log_receipt(receipt)

    def _log_receipt(self, receipt: DeliveryReceipt) -> None:
        logger.info(
            "delivery_receipt",
            channel=receipt.channel.value,
            delivered=receipt.delivered,
            retryable=receipt.retryable,
        )

    async def _load_user(self, session: AsyncSession, user_id: str) -> User | None:
        try:
            return await SqlAlchemyUserStore(session).get(user_id=user_id)
        except UserNotFoundError:
            return None

    async def _persist_turn(self, session: AsyncSession, *, user_id: str, text: str) -> None:
        """Write the scheduled brief into the owner's thread history, same as an interactive
        turn — a scheduled run shows up in the user's threads like any other answer."""
        store = SqlAlchemyConversationStore(session)
        thread = await store.create_thread(user_id=user_id, title="排程摘要")
        await store.append_turn(
            user_id=user_id,
            thread_id=thread.id,
            role=TurnRole.ASSISTANT,
            blocks=[
                (MessageBlockType.TEXT, TextBlock(text=text)),
                (
                    MessageBlockType.CAVEAT,
                    CaveatBlock(kind="scheduled", text="排程觀察，非投資建議。"),
                ),
            ],
            parent_message_id=None,
        )

    def _emit(self, kind: str, *, user_id: str, data: dict[str, object]) -> None:
        if self._ctx.event_bus is not None:
            self._ctx.event_bus.publish(kind, data={"user_id": user_id, **data})

    def _job_message(self, job: ScheduleJob) -> str:
        inputs = job.action.inputs
        symbols = inputs.get("stock_ids") or inputs.get("symbols")
        if symbols:
            return f"排程執行技能 {job.action.skill_ref}，標的：{symbols}"
        return f"排程執行技能 {job.action.skill_ref}"

    def _daily_limit_reached(self, delivered_today: int, daily_limit: int | None) -> bool:
        return daily_limit is not None and delivered_today >= daily_limit

    def _next_run(self, job: ScheduleJob, now: datetime) -> datetime:
        """Coarse next-fire time. A concrete cron/market-calendar resolver is a later infra
        concern; a scheduled job's cadence is at least a day apart, so advancing by one day is a
        safe floor that never produces a tight loop."""
        del job
        return now + timedelta(days=1)


def _capability_for(market: Market) -> Capability:
    del market
    return Capability.PRICE_EOD


def build_scheduler_worker(  # noqa: PLR0913 — one seam per injected collaborator
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Orchestrator,
    router: DataAccessRouter,
    entitlement_resolver: EntitlementResolver,
    channels: dict[DeliveryChannelKind, DeliveryChannel] | None = None,
    leader: LeaderElection | None = None,
    quota_gate: QuotaGate | None = None,
    event_bus: EventBus | None = None,
) -> SchedulerWorker:
    """Assemble a worker with the single-process defaults (always-leader). The quota gate defaults
    to the real `UsageQuotaGate` over the same durable daily-chat counter interactive turns use, so
    a scheduled run consumes the owner's tier quota exactly like a chat; a deployment may still
    override `leader`/`quota_gate` at this construction site — never in the loop body."""
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=orchestrator,
            router=router,
            channels=channels or {},
            entitlement_resolver=entitlement_resolver,
            leader=leader or get_default_leader_election(),
            quota_gate=quota_gate or UsageQuotaGate(SqlAlchemyUsageStore(session_factory)),
            event_bus=event_bus,
        )
    )


__all__ = [
    "SchedulerContext",
    "SchedulerWorker",
    "build_scheduler_worker",
]
