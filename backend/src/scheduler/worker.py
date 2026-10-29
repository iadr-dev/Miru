"""The scheduler worker: the job-firing / alert-evaluation loop.

A scheduled run is just an agent turn with no live human. This worker enforces, in code, every
bound the scheduling amendment is permitted under:

1. **Leader-only.** `LeaderElection.is_leader()` is checked before anything fires. Not being the
   leader is a silent skip, never an error — the same contract the ingest write path uses, so a
   real cross-process election swaps in at the construction site alone.
2. **User-authorized.** Only jobs/alerts a signed-in user created are in the store; the worker
   never invents work.
3. **Open to every user, data-masked at run time.** The feature is not tier-gated (modules/14);
   a job whose owner account no longer exists auto-disables. The owner's tier is still resolved
   *fresh* each run: sponsor data inside a run is masked by the same tool-dispatch gate an
   interactive turn uses, because the run's `ToolContext` carries the owner's current
   data-licensing tier — a lapsed sponsor entitlement masks that section of the brief without
   spamming an upgrade prompt.
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

import asyncio
from calendar.holidays import is_market_open_day
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.byok import ByokKeyRequiredError, resolve_byok_override
from accounts.entitlements import feature_allowed
from accounts.hooks import EntitlementResolver
from accounts.models import User
from accounts.store import SqlAlchemyUserStore, UserNotFoundError
from agent.loop import RichCard
from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from analytics.watchlist_review import NOTHING_TO_REVIEW
from config.settings import Settings
from conversation.models import CaveatBlock, MessageBlockType, RichCardBlock, TextBlock, TurnRole
from conversation.store import SqlAlchemyConversationStore, ThreadNotFoundError
from dataaccess.router import DataAccessRouter
from events.bus import EventBus
from events.kinds import EventKind
from governance.textscan import check_output
from ingest.jobs.strategy_intraday import STRATEGY_HITS_DATASET
from ingest.leader import LeaderElection, get_default_leader_election
from llm.registry import ByokOverride, byok_scope
from orchestrator.models import OrchestratorResult
from orchestrator.orchestrator import Orchestrator, PersonalityContext
from scheduler.delivery.base import (
    DeliveryChannel,
    DeliveryNotification,
    DeliveryReceipt,
    DeliveryRecipient,
)
from scheduler.delivery.layout import Layout, render_text, trim_layout
from scheduler.evaluator import Evaluation, SnapshotReader, SnapshotView, evaluate_condition
from scheduler.models import (
    AlertPreference,
    AlertRule,
    DeliveryChannelKind,
    JobStatus,
    Notification,
    NotificationSource,
    PendingDelivery,
    QuietHours,
    ScheduleJob,
    StrategySubscription,
    TriggerMode,
)
from scheduler.notification_messages import (
    brief_layout,
    brief_preview,
    digest_body,
    digest_layout,
    held_layout,
    report_layout,
    report_preview,
    safe_alert_message,
)
from scheduler.notification_payload import read_layout
from scheduler.quota import QuotaGate, UsageQuotaGate
from scheduler.retention import prune_stream_events
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.strategy_messages import (
    first_alertable,
    hit_alert,
    hit_alert_layout,
    in_scope,
    morning_list,
    morning_list_layout,
    safe_message,
)
from scheduler.strategy_store import STRATEGY_SOURCE_PREFIX, StrategySubscriptionStore
from scheduler.templates import (
    EVENT_KINDS,
    NON_ADVICE_FOOTER,
    capability_of,
    describe,
    probe_dataset,
)
from scheduler.triggers import UnresolvableTriggerError, market_timezone, next_run_after
from scheduler.unsubscribe import unsubscribe_url
from schemas.enums import Capability, Market
from schemas.schedule_templates import (
    MAX_TARGETS,
    WATCHLIST_INPUT,
    is_weekly_review,
    schedule_label,
)
from tools.base import Tool, dispatch_tool
from tools.morning_brief_models import GetMorningBriefOutput, MorningBriefInput
from usage.llm_usage import write_model_usage
from usage.store import SqlAlchemyUsageStore, write_turn_usage

logger = structlog.get_logger(__name__)

_DEFAULT_USER_TZ = "Asia/Taipei"
# The TW session ends at 13:30; a morning list that would arrive after it is not sent.
_SESSION_CLOSE = time(13, 30)
_HELD_LOOKBACK = timedelta(days=2)
# External delivery: a transient failure is retried with a growing delay, up to this many attempts
# in all, then the notification is marked failed (the in-app entry is unaffected).
_MAX_DELIVERY_ATTEMPTS = 6
_RETRY_BASE_DELAY = timedelta(minutes=5)
_RETRY_MAX_DELAY = timedelta(hours=6)
_RETRY_BATCH = 100
# While one retry sweep sends a claimed notification, nothing else may pick it up for this long.
_RETRY_LEASE = timedelta(minutes=10)
# The morning digest goes out at or after this local time, and no later than the cutoff (a pass that
# was down at 08:00 still sends it late in the morning; after that the moment has passed).
_DIGEST_FROM = time(8, 0)
_DIGEST_UNTIL = time(12, 0)
_DIGEST_LOOKBACK = timedelta(hours=24)
_LAYOUT_CHANNELS = frozenset(
    {
        DeliveryChannelKind.IN_APP,
        DeliveryChannelKind.TELEGRAM,
        DeliveryChannelKind.LINE,
        DeliveryChannelKind.EMAIL,
    }
)


def _user_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or _DEFAULT_USER_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(_DEFAULT_USER_TZ)


def _within_quiet_hours(quiet: QuietHours | None, now: datetime, tz_name: str | None) -> bool:
    """Is `now` inside the quiet window? The window is wall-clock time in the user's own timezone
    (their preference, default Asia/Taipei), not the server's UTC."""
    if quiet is None:
        return False
    hhmm = as_utc(now).astimezone(_user_zone(tz_name)).strftime("%H:%M")
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
    # Read-only view of the ingested store the alert engine evaluates against. None disables
    # alert evaluation (job scheduling is unaffected).
    reader: SnapshotReader | None = None
    # The `get_morning_brief` tool. The 晨間摘要 is rendered by code from its figures, so a daily
    # digest needs no model call and no chat quota. None falls back to the plain recap.
    brief_tool: Tool | None = None
    # Public HTTPS base URL and signing secret for the one-click email unsubscribe link.
    public_base_url: str | None = None
    link_secret: str | None = None
    # The web app's public base URL; a channel only gets it when it is HTTPS.
    app_base_url: str | None = None
    # Needed to resolve the owner's own LLM key (BYOK); None runs every job on the platform key.
    settings: Settings | None = None


_PRUNE_EVERY = timedelta(hours=1)


class SchedulerWorker:
    def __init__(self, ctx: SchedulerContext) -> None:
        self._ctx = ctx
        # One DuckDB reader connection must not serve concurrent queries, so alert evaluation is
        # serialised through this lock.
        self._eval_lock = asyncio.Lock()
        self._last_pruned: datetime | None = None

    @property
    def leader(self) -> LeaderElection:
        return self._ctx.leader

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        """Delete data past its retention (the SSE replay log). The pass runs every few seconds;
        this does the work at most once per `_PRUNE_EVERY`, since nothing here is time-critical."""
        now = now or datetime.now(UTC)
        if self._last_pruned is not None and now - self._last_pruned < _PRUNE_EVERY:
            return 0
        self._last_pruned = now
        return await prune_stream_events(self._ctx.session_factory, now=now)

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

    async def run_job(  # noqa: PLR0911, PLR0915 — each early return is a distinct skip reason
        self, job: ScheduleJob, *, now: datetime | None = None
    ) -> bool:
        """Run one job end to end. Returns True if the skill actually executed (vs. skipped)."""
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            user = await self._load_user(session, job.user_id)

            if user is None:
                # Owner account is gone — auto-disable so the orphaned job never fires again.
                await store.set_job_status(job_id=job.id, status=JobStatus.ERROR, disable=True)
                await session.commit()
                self._emit(
                    EventKind.SCHEDULE_DISABLED, user_id=job.user_id, data={"job_id": job.id}
                )
                logger.info("schedule_job_auto_disabled", job_id=job.id, reason="owner_missing")
                return False

            prefs = await store.get_preferences(user_id=user.id)
            tz_name = prefs.timezone if prefs else None
            quiet = job.quiet_hours or (prefs.quiet_hours if prefs else None)
            if _within_quiet_hours(quiet, now, tz_name):
                # The job stays due, so it runs on the first pass after the window closes.
                self._emit(EventKind.SCHEDULE_SKIPPED, user_id=user.id, data={"job_id": job.id})
                return False

            # A user with saved LLM keys runs on THEIR key (and so is not held to the chat cap);
            # one whose keys don't cover the default model is skipped, never moved onto ours.
            try:
                byok = await self._byok_for(session, user)
            except ByokKeyRequiredError:
                await store.set_job_status(
                    job_id=job.id,
                    status=JobStatus.SKIPPED_QUOTA,
                    last_run=now,
                    next_run=self._next_run(job, now),
                    reason="byok_key_required",
                )
                await session.commit()
                self._emit(EventKind.SCHEDULE_SKIPPED, user_id=user.id, data={"job_id": job.id})
                logger.info("schedule_job_skipped_byok_key", job_id=job.id)
                return False

            if await self._nothing_to_review(store, session, job, user, now):
                return False

            if byok is None and not await self._ctx.quota_gate.allows(
                user_id=user.id, tier=user.tier
            ):
                # Advance next_run too: with a real resolver the job stays "due" otherwise, and
                # an over-quota job must be skipped once per cadence, not retried every poll.
                await store.set_job_status(
                    job_id=job.id,
                    status=JobStatus.SKIPPED_QUOTA,
                    last_run=now,
                    next_run=self._next_run(job, now),
                    reason="chat_quota_exhausted",
                )
                await session.commit()
                self._emit(
                    EventKind.SCHEDULE_SKIPPED_QUOTA, user_id=user.id, data={"job_id": job.id}
                )
                logger.info("schedule_job_skipped_quota", job_id=job.id)
                return False

            if self._daily_limit_reached(
                await store.deliveries_today(
                    user_id=user.id, source_id=job.id, trigger_date=now.date()
                ),
                job.daily_limit,
            ):
                self._emit(
                    EventKind.SCHEDULE_SKIPPED_LIMIT, user_id=user.id, data={"job_id": job.id}
                )
                return False

            inputs = await self._resolved_inputs(store, job)
            if inputs is None:
                # The watchlist is gone or holds no Taiwan stocks: nothing to brief on. Say so and
                # move on to the next cadence instead of running the skill on an empty target.
                await store.set_job_status(
                    job_id=job.id,
                    status=JobStatus.ERROR,
                    last_run=now,
                    next_run=self._next_run(job, now),
                    reason="watchlist_empty",
                )
                await session.commit()
                self._emit(EventKind.SCHEDULE_SKIPPED, user_id=user.id, data={"job_id": job.id})
                return False

            # Take the job atomically. `due_jobs` is only a scan, so a second worker (a restart
            # overlap, a lease handover) may be looking at the same job; whoever wins this claim
            # runs it, the other stands down. It also marks the job running, so a crash mid-run is
            # detectable as an orphaned run.
            if not await store.claim_job(job_id=job.id, now=now):
                await session.rollback()
                logger.info("schedule_job_claim_lost", job_id=job.id)
                return False
            await session.commit()

            tool_context = self._ctx.entitlement_resolver.tool_context_for(user)
            self._emit(
                EventKind.SCHEDULE_RUN,
                user_id=user.id,
                data={"job_id": job.id, "skill": job.action.skill_ref},
            )
            try:
                with byok_scope(byok):
                    result = await self._ctx.orchestrator.run_skill(
                        job.action.skill_ref,
                        self._job_message(job, inputs),
                        tool_context=tool_context,
                        event_bus=self._ctx.event_bus,
                        turn_id=job.id,
                        # A scheduled run has no live thread to carry a per-thread override, so
                        # the owner's own personality preference is the only resolvable level
                        # below the default — same precedence the interactive path uses, just
                        # missing the (absent) thread tier.
                        personality=PersonalityContext(user_preference=user.personality_id),
                    )
            except Exception as exc:
                await store.set_job_status(
                    job_id=job.id,
                    status=JobStatus.ERROR,
                    last_run=now,
                    next_run=self._next_run(job, now),
                    reason=type(exc).__name__,
                )
                await session.commit()
                logger.warning("schedule_job_run_failed", job_id=job.id, error=str(exc))
                return False

            thread_id = await self._persist_turn(
                session,
                user_id=user.id,
                text=result.final_text,
                cards=result.rich_cards,
                job=job,
            )
            await self._record_usage(
                session,
                user_id=user.id,
                run_id=f"{job.id}:{now.isoformat()}",
                result=result,
                byok=byok is not None,
            )
            await self._deliver(
                store,
                user=user,
                source=NotificationSource.SCHEDULE,
                source_id=job.id,
                channels=job.delivery_channels,
                title=schedule_label(job.action.skill_ref),
                body=report_preview(result.final_text),
                trigger_date=now.date(),
                deep_link={"route": "thread", "thread_id": thread_id},
                layout=report_layout(result.final_text, schedule_label(job.action.skill_ref)),
            )
            await store.set_job_status(
                job_id=job.id,
                status=JobStatus.OK,
                last_run=now,
                next_run=self._next_run(job, now),
                thread_id=thread_id,
            )
            await session.commit()
            return True

    # ---- alerts ----
    #
    # Event-driven (modules/14): a `data.updated` signal and the reconciliation sweep both land in
    # `_evaluate_group`, which is idempotent per `(capability, market, as_of)` via the eval mark and
    # the notification dedupe key. The signal is best-effort, so the sweep — run every daemon pass —
    # is the correctness floor; the signal only lowers latency.

    async def reconcile_alerts(self, *, now: datetime | None = None) -> list[str]:
        """Evaluate, for every `(capability, market)` an enabled rule depends on, the latest
        ingested `as_of` not yet evaluated. Returns the ids of rules that fired."""
        if not self._ctx.leader.is_leader():
            logger.info("scheduler_skip_not_leader", phase="alerts")
            return []
        if self._ctx.reader is None:
            return []
        now = now or datetime.now(UTC)
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            wanted: set[tuple[Capability, Market]] = set()
            for rule in await store.enabled_alerts():
                for _symbol, market in await self._targets(store, rule):
                    wanted.add((capability_of(rule.condition), market))
            await session.commit()
        fired: list[str] = []
        for capability, market in sorted(wanted, key=lambda w: (w[0].value, w[1].value)):
            as_of = await self._latest_as_of(capability, market, now)
            if as_of is not None:
                fired.extend(await self._evaluate_group(capability, market, as_of, now=now))
        return fired

    async def handle_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, now: datetime | None = None
    ) -> list[str]:
        """React to a `data.updated` signal. A malformed `as_of` is ignored (best-effort signal)."""
        if not self._ctx.leader.is_leader() or self._ctx.reader is None:
            return []
        try:
            day = date.fromisoformat(as_of)
        except ValueError:
            logger.warning("alert_signal_bad_as_of", as_of=as_of)
            return []
        return await self._evaluate_group(capability, market, day, now=now or datetime.now(UTC))

    async def _latest_as_of(
        self, capability: Capability, market: Market, now: datetime
    ) -> date | None:
        reader = self._ctx.reader
        if reader is None:
            return None
        probe = probe_dataset(capability, market)
        today = now.astimezone(market_timezone(market)).date()
        async with self._eval_lock:
            return await asyncio.to_thread(
                SnapshotView(reader).latest_partition_date, probe, market, on_or_before=today
            )

    async def _targets(
        self, store: SqlAlchemySchedulerStore, rule: AlertRule
    ) -> list[tuple[str, Market]]:
        if rule.symbol is not None and rule.market is not None:
            return [(rule.symbol, rule.market)]
        if rule.watchlist_id is not None:
            return await store.watchlist_members(
                watchlist_id=rule.watchlist_id, user_id=rule.user_id
            )
        return []

    async def _evaluate_group(
        self, capability: Capability, market: Market, as_of: date, *, now: datetime
    ) -> list[str]:
        reader = self._ctx.reader
        if reader is None:
            return []
        async with self._eval_lock, self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            mark = await store.get_eval_mark(capability=capability.value, market=market.value)
            if mark is not None and as_of.isoformat() <= mark:
                return []
            rules = [
                r for r in await store.enabled_alerts() if capability_of(r.condition) is capability
            ]
            targets = {r.id: await self._targets(store, r) for r in rules}
            view = SnapshotView(reader)
            evaluations = await asyncio.to_thread(
                _compute_evaluations, rules, targets, market, as_of, view
            )
            fired: list[str] = []
            for rule in rules:
                # One rule at a time, each committed on its own: a rule that raises loses only its
                # own uncommitted work, and the rest of the group (and every other user's rules in
                # it) still evaluate. Before, one bad rule rolled the whole group back and it was
                # retried, and failed, on every pass.
                try:
                    delivered = await self._apply_rule(
                        store,
                        rule,
                        results=evaluations.get(rule.id, {}),
                        market=market,
                        as_of=as_of,
                        now=now,
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("alert_rule_failed", rule_id=rule.id)
                    continue
                if delivered:
                    fired.append(rule.id)
            await store.set_eval_mark(
                capability=capability.value, market=market.value, as_of=as_of.isoformat()
            )
            await session.commit()
        return fired

    async def _apply_rule(  # noqa: PLR0913 — one argument per evaluation dimension
        self,
        store: SqlAlchemySchedulerStore,
        rule: AlertRule,
        *,
        results: dict[str, Evaluation],
        market: Market,
        as_of: date,
        now: datetime,
    ) -> bool:
        """Apply one rule's per-symbol evaluations: expiry, edge-trigger, cooldown, mode, delivery,
        and the state write-back. Returns whether anything was delivered."""
        if rule.expires_at is not None and now > as_utc(rule.expires_at):
            await store.save_alert_state(
                alert_id=rule.id, state=rule.state, enabled=False, auto_paused_reason="expired"
            )
            return False
        # Partition dates are market-local, so compare in the market's timezone.
        if as_utc(rule.created_at).astimezone(market_timezone(market)).date() > as_of:
            return False  # never fire on data that predates the rule

        user = await self._load_user(store.session, rule.user_id)
        if user is None:
            await store.save_alert_state(
                alert_id=rule.id,
                state=rule.state,
                enabled=False,
                auto_paused_reason="owner_missing",
            )
            return False

        state = dict(rule.state)
        met_map = _met_map(state)
        fire_count = _as_int(state.get("fire_count"))
        channels, hold_external = await _delivery_settings(
            store, user_id=rule.user_id, channels=rule.channels, quiet=rule.quiet_hours, at=now
        )
        delivered = False

        for symbol, evaluation in results.items():
            was_met = met_map.get(symbol, False)
            met_map[symbol] = evaluation.met
            if not _should_fire(rule, evaluation, was_met=was_met, state=state, now=now):
                continue
            await self._fire_alert(
                store,
                rule=rule,
                user=user,
                symbol=symbol,
                evaluation=evaluation,
                channels=channels,
                trigger_date=as_of,
                hold_external=hold_external,
            )
            fire_count += 1
            state["last_fired_at"] = now.isoformat()
            delivered = True

        state["met"] = met_map
        state["fire_count"] = fire_count
        completed = delivered and rule.trigger_mode is TriggerMode.ONCE
        await store.save_alert_state(
            alert_id=rule.id,
            state=state,
            last_triggered=now if delivered else None,
            enabled=False if completed else None,
            auto_paused_reason="completed" if completed else None,
        )
        return delivered

    async def _fire_alert(  # noqa: PLR0913 — one argument per delivery dimension
        self,
        store: SqlAlchemySchedulerStore,
        *,
        rule: AlertRule,
        user: User,
        symbol: str,
        evaluation: Evaluation,
        channels: tuple[DeliveryChannelKind, ...],
        trigger_date: date,
        hold_external: bool,
    ) -> None:
        self._emit(
            EventKind.ALERT_FIRED, user_id=user.id, data={"alert_id": rule.id, "symbol": symbol}
        )
        body, layout = safe_alert_message(evaluation, trigger_date)
        await self._deliver(
            store,
            user=user,
            source=NotificationSource.ALERT,
            source_id=rule.id if rule.symbol else f"{rule.id}:{symbol}",
            channels=channels,
            title=f"{symbol} - {describe(rule.condition)}",
            body=body,
            trigger_date=trigger_date,
            deep_link={"route": "stock", "symbol": symbol},
            hold_external=hold_external,
            layout=layout,
        )

    # ---- shared internals ----

    async def _deliver(  # noqa: PLR0913 — one field per delivery dimension
        self,
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
        hold_external: bool = False,
        layout: Layout | None = None,
    ) -> None:
        """Persist the in-app record (the dedupe guard, and the inbox entry) then route to each
        configured external channel. A redelivery of the same `(source_id, trigger_date, channel)`
        is a no-op via the unique dedupe key — at-least-once execution stays idempotent.

        During the user's quiet hours (`hold_external`) the inbox entry is still written at once
        (it is silent) but external channels are only recorded as held; `release_held` sends them
        as one digest once the window closes.

        `layout` is the structured twin of `body`. The inbox, Telegram, LINE and email rows store
        it (cut to a bounded size), so a retry sends the same layout and the bell can draw it; the
        push row does not. It reaches a channel only if the text it renders passes the same output
        scan as the body."""
        layout = _scanned_layout(layout)
        selected = _channel_order(channels)
        recipient = await self._recipient_for(store, user)
        for channel_kind in selected:
            held = hold_external and channel_kind is not DeliveryChannelKind.IN_APP
            external = channel_kind is not DeliveryChannelKind.IN_APP and not held
            record = await store.record_notification(
                user_id=user.id,
                source=source,
                source_id=source_id,
                channel=channel_kind,
                trigger_date=trigger_date,
                payload=_notification_payload(
                    title,
                    body,
                    deep_link,
                    held=held,
                    layout=layout if channel_kind in _LAYOUT_CHANNELS else None,
                ),
                delivery_state="pending" if external else "sent",
            )
            if record is None:
                continue  # dedupe no-op — already delivered on this channel today
            # The row is both the dedupe guard and the retry queue, so it is durable BEFORE anything
            # is sent. A failure after this point can no longer cause a second send, and a crash
            # before the send leaves a pending row the retry sweep delivers.
            await store.session.commit()
            if held:
                continue
            channel = self._ctx.channels.get(channel_kind)
            if channel is None:
                if external:  # not configured in this deployment: nothing will ever send it
                    await _fail_unsendable(store, record.id)
                continue
            notification = DeliveryNotification(
                notification_id=record.id,
                title=title,
                body=_external_body(body),
                deep_link=deep_link,
                layout=layout,
            )
            await self._send_and_record(
                store,
                user_id=user.id,
                record_id=record.id,
                channel=channel,
                notification=notification,
                recipient=recipient,
                attempts=0,
                track=external,
            )

    async def _send_and_record(  # noqa: PLR0913 — one argument per delivery dimension
        self,
        store: SqlAlchemySchedulerStore,
        *,
        user_id: str,
        record_id: str,
        channel: DeliveryChannel,
        notification: DeliveryNotification,
        recipient: DeliveryRecipient,
        attempts: int,
        track: bool,
    ) -> str:
        """Send one notification, then record what happened and commit. A channel that raises is
        a transient failure like any other. Returns the notification's new delivery state."""
        try:
            receipt = await channel.send(notification, recipient)
        except Exception:
            logger.warning(
                "delivery_send_raised",
                channel=channel.kind.value,
                notification_id=record_id,
                exc_info=True,
            )
            receipt = DeliveryReceipt(
                channel=channel.kind, delivered=False, retryable=True, detail="send_raised"
            )
        self._log_receipt(receipt)
        await self._prune_stale(store, user_id, receipt)
        state, next_attempt = _delivery_outcome(receipt, attempts, datetime.now(UTC))
        if track:
            await store.mark_delivery(
                notification_id=record_id,
                state=state,
                attempts=attempts + 1,
                next_attempt_at=next_attempt,
            )
        await store.session.commit()
        return state

    async def retry_pending_deliveries(self, *, now: datetime | None = None) -> int:
        """Deliver what an earlier attempt left pending: a transient failure whose retry time has
        come, and any row recorded but never sent because the process died first. Returns how many
        were delivered. Rows are claimed (and the claim committed) before any send, so no database
        lock is held across a network call."""
        if not self._ctx.leader.is_leader():
            return 0
        now = now or datetime.now(UTC)
        delivered = 0
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            due = await store.claim_due_deliveries(now=now, limit=_RETRY_BATCH, lease=_RETRY_LEASE)
            await session.commit()
            for item in due:
                try:
                    if await self._retry_one(store, session, item) == "sent":
                        delivered += 1
                except Exception:
                    await session.rollback()
                    logger.exception("delivery_retry_failed", notification_id=item.id)
        return delivered

    async def _retry_one(
        self, store: SqlAlchemySchedulerStore, session: AsyncSession, item: PendingDelivery
    ) -> str:
        user = await self._load_user(session, item.user_id)
        channel = self._ctx.channels.get(item.channel)
        if user is None or channel is None:
            await store.mark_delivery(
                notification_id=item.id,
                state="failed",
                attempts=item.attempts,
                next_attempt_at=None,
            )
            await session.commit()
            return "failed"
        raw_link = item.payload.get("deep_link")
        deep_link = cast("dict[str, object]", raw_link) if isinstance(raw_link, dict) else {}
        notification = DeliveryNotification(
            notification_id=item.id,
            title=str(item.payload.get("title", "")),
            body=_external_body(str(item.payload.get("body", ""))),
            deep_link=deep_link,
            layout=read_layout(item.payload.get("layout")),
        )
        return await self._send_and_record(
            store,
            user_id=user.id,
            record_id=item.id,
            channel=channel,
            notification=notification,
            recipient=await self._recipient_for(store, user),
            attempts=item.attempts,
            track=True,
        )

    async def run_strategy_watch(self, *, now: datetime | None = None) -> int:
        """策略盯盤 deliveries for today: each enabled subscription's morning list (once, before the
        close) and one alert per (stock, strategy) the intraday check marked alertable. Reads the
        two stored partitions only; the dedupe key makes every pass idempotent, so this runs on each
        scheduler pass. Returns how many subscriptions were processed."""
        reader = self._ctx.reader
        if not self._ctx.leader.is_leader() or reader is None:
            return 0
        now = now or datetime.now(UTC)
        local = now.astimezone(market_timezone(Market.TW))
        today = local.date()
        async with self._eval_lock:
            if not await asyncio.to_thread(
                reader.has_partition, STRATEGY_WATCHLIST_DATASET, Market.TW.value, today
            ):
                return 0
            watch_rows = await asyncio.to_thread(
                reader.read_partition, STRATEGY_WATCHLIST_DATASET, Market.TW.value, today
            )
            hit_rows = (
                await asyncio.to_thread(
                    reader.read_partition, STRATEGY_HITS_DATASET, Market.TW.value, today
                )
                if await asyncio.to_thread(
                    reader.has_partition, STRATEGY_HITS_DATASET, Market.TW.value, today
                )
                else []
            )
        processed = 0
        async with self._ctx.session_factory() as session:
            subscriptions = await StrategySubscriptionStore(session).enabled()
            for subscription in subscriptions:
                try:
                    await self._deliver_strategy_watch(
                        session, subscription, watch_rows, hit_rows, local=local
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("strategy_watch_failed", subscription_id=subscription.id)
                    continue
                processed += 1
        return processed

    async def _deliver_strategy_watch(
        self,
        session: AsyncSession,
        subscription: StrategySubscription,
        watch_rows: list[dict[str, object]],
        hit_rows: list[dict[str, object]],
        *,
        local: datetime,
    ) -> None:
        store = SqlAlchemySchedulerStore(session)
        strategies_store = StrategySubscriptionStore(session)
        user = await self._load_user(session, subscription.user_id)
        if user is None:
            return
        members = await _subscription_members(store, subscription, user.id)
        channels, hold = await _delivery_settings(
            store,
            user_id=user.id,
            channels=subscription.channels,
            quiet=subscription.quiet_hours,
            at=local,
        )
        today = local.date()
        watch = in_scope(watch_rows, subscription.strategies, members)
        if subscription.morning_list and local.time() < _SESSION_CLOSE:
            await self._deliver_morning_list(
                store,
                user=user,
                subscription=subscription,
                watch=watch,
                today=today,
                channels=channels,
                hold=hold,
            )
        # A subscription made today only hears about slots after it existed.
        created = as_utc(subscription.created_at).astimezone(local.tzinfo)
        not_before = created.time() if created.date() == today else None
        watch_by_key = {(str(r.get("symbol")), str(r.get("strategy"))): r for r in watch}
        masked = not feature_allowed(user, "tick_minute_k")
        scoped_hits = in_scope(hit_rows, subscription.strategies, members)
        intraday = first_alertable(scoped_hits, not_before=not_before)
        closes = first_alertable(scoped_hits, close=True)
        for hit, suffix in [*((h, "") for h in intraday), *((h, ":close") for h in closes)]:
            limit = subscription.daily_limit
            sent = await strategies_store.alerts_sent(user_id=user.id, day=today)
            if limit is not None and sent >= limit:
                break
            symbol, strategy = str(hit.get("symbol")), str(hit.get("strategy"))
            row = watch_by_key.get((symbol, strategy))
            title, body, plain = hit_alert(hit, row, masked=masked)
            text, layout = safe_message(
                [
                    (body, hit_alert_layout(hit, row, masked=masked)),
                    (plain, hit_alert_layout(hit, row, masked=masked, with_headline=False)),
                ],
                f"{symbol} 你訂閱的條件全數成立，請開啟 Miru 查看。",
            )
            await self._deliver(
                store,
                user=user,
                source=NotificationSource.ALERT,
                source_id=f"{STRATEGY_SOURCE_PREFIX}{subscription.id}:{symbol}:{strategy}{suffix}",
                channels=channels,
                title=title,
                body=text,
                trigger_date=today,
                deep_link={"route": "watch", "symbol": symbol, "strategy": strategy},
                hold_external=hold,
                layout=layout,
            )

    async def _deliver_morning_list(  # noqa: PLR0913 — one argument per delivery dimension
        self,
        store: SqlAlchemySchedulerStore,
        *,
        user: User,
        subscription: StrategySubscription,
        watch: Sequence[Mapping[str, object]],
        today: date,
        channels: tuple[DeliveryChannelKind, ...],
        hold: bool,
    ) -> None:
        listing = morning_list(watch, today)
        if listing is None:
            return
        title, body = listing
        text, layout = safe_message(
            [(body, morning_list_layout(watch, today))], "今日盯盤清單已更新，請開啟 Miru 查看。"
        )
        await self._deliver(
            store,
            user=user,
            source=NotificationSource.ALERT,
            source_id=f"{STRATEGY_SOURCE_PREFIX}{subscription.id}:morning",
            channels=channels,
            title=title,
            body=text,
            trigger_date=today,
            deep_link={"route": "watch"},
            hold_external=hold,
            layout=layout,
        )

    async def send_morning_digests(self, *, now: datetime | None = None) -> int:
        """Send the 晨間摘要 to every user who turned it on, once per local day: on a Taiwan
        trading day, from 08:00 (their timezone) — later if their quiet hours are still running —
        until noon.

        It is a plain recap of what happened in the last 24 hours (the notifications that fired)
        plus how many alerts and schedules are active. It is deterministic: no model call, so it
        costs no chat quota and cannot drift into advice. Nothing to report means no digest.
        Returns how many were sent."""
        if not self._ctx.leader.is_leader():
            return 0
        now = now or datetime.now(UTC)
        sent = 0
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            for user_id in await store.morning_digest_user_ids():
                try:
                    delivered = await self._send_digest_to(store, session, user_id, now)
                    await session.commit()  # this user's digest is recorded before the next one
                except Exception:
                    await session.rollback()
                    logger.exception("morning_digest_failed", user_id=user_id)
                    continue
                if delivered:
                    sent += 1
        return sent

    async def _send_digest_to(
        self,
        store: SqlAlchemySchedulerStore,
        session: AsyncSession,
        user_id: str,
        now: datetime,
    ) -> bool:
        prefs = await store.get_preferences(user_id=user_id)
        local_day = _digest_day(prefs, now)
        if prefs is None or local_day is None:
            return False
        source_id = f"digest:{user_id}"
        if await store.deliveries_today(
            user_id=user_id, source_id=source_id, trigger_date=local_day
        ):
            return False  # already sent today
        user = await self._load_user(session, user_id)
        if user is None:
            return False
        brief = await self._run_brief(store, session, user, prefs)
        if brief is not None:
            text, thread_id = brief
            await self._deliver(
                store,
                user=user,
                source=NotificationSource.DIGEST,
                source_id=source_id,
                channels=prefs.channels,
                title=f"晨間摘要 {local_day.isoformat()}",
                body=brief_preview(text),
                trigger_date=local_day,
                deep_link={"route": "thread", "thread_id": thread_id},
                layout=brief_layout(text),
            )
            return True
        recent = await store.recent_inbox(user_id=user_id, since=now - _DIGEST_LOOKBACK)
        active_alerts = sum(1 for a in await store.list_alerts(user_id=user_id) if a.enabled)
        active_jobs = sum(1 for j in await store.list_jobs(user_id=user_id) if j.enabled)
        if not recent and not (active_alerts or active_jobs):
            return False
        titles = [str(item.payload.get("title", "")) for item in recent]
        await self._deliver(
            store,
            user=user,
            source=NotificationSource.DIGEST,
            source_id=source_id,
            channels=prefs.channels,
            title=f"晨間摘要 {local_day.isoformat()}",
            body=digest_body(titles, active_alerts, active_jobs),
            trigger_date=local_day,
            deep_link={"route": "notifications"},
            layout=digest_layout(titles, active_alerts, active_jobs),
        )
        return True

    async def _run_brief(
        self,
        store: SqlAlchemySchedulerStore,
        session: AsyncSession,
        user: User,
        prefs: AlertPreference,
    ) -> tuple[str, str] | None:
        """Build today's full report (market data + the user's own watchlist stocks and ETFs) and
        append it, with its charts, to the user's one 晨間摘要 thread. The text is rendered by code
        from the tool's figures — no model call, so no quota and nothing to drift into advice. If
        the tool is missing or fails, `None` and the caller falls back to the plain recap."""
        tool = self._ctx.brief_tool
        if tool is None:
            return None
        symbols = await store.brief_symbols(user_id=user.id)
        try:
            result = await dispatch_tool(
                tool,
                MorningBriefInput.model_validate(symbols.model_dump()),
                self._ctx.entitlement_resolver.tool_context_for(user),
            )
        except Exception:
            logger.warning("morning_brief_run_failed", user_id=user.id)
            return None
        if not isinstance(result, GetMorningBriefOutput) or not result.report:
            return None
        thread_id = await self._append_to_thread(
            session,
            user_id=user.id,
            thread_id=prefs.digest_thread_id,
            title="晨間摘要",
            text=result.report,
            cards=[RichCard(card_type="morning_brief", data=result.model_dump(mode="json"))],
            caveat="資料整理，非即時報價，不構成投資建議。",
        )
        if thread_id != prefs.digest_thread_id:
            await store.set_digest_thread(user_id=user.id, thread_id=thread_id)
        return result.report, thread_id

    async def _recipient_for(
        self, store: SqlAlchemySchedulerStore, user: User
    ) -> DeliveryRecipient:
        """Where this user can be reached: their account email, registered devices, the LINE id from
        their LINE sign-in, and the Telegram chat they linked (if any)."""
        return DeliveryRecipient(
            user_id=user.id,
            # An address that hard-bounced or complained is never emailed again.
            email_address=(
                None
                if user.email and await store.is_email_suppressed(address=user.email)
                else user.email
            ),
            device_tokens=tuple(d.token for d in await store.list_devices(user_id=user.id)),
            unsubscribe_url=self._unsubscribe_url(user),
            app_url=self._app_url(),
            line_user_id=await store.linked_line_user_id(user_id=user.id),
            telegram_chat_id=await store.channel_address(user_id=user.id, channel="telegram"),
        )

    def _app_url(self) -> str | None:
        base = self._ctx.app_base_url
        return base.rstrip("/") if base and base.startswith("https://") else None

    def _unsubscribe_url(self, user: User) -> str | None:
        if not (user.email and self._ctx.public_base_url and self._ctx.link_secret):
            return None
        return unsubscribe_url(
            self._ctx.public_base_url, self._ctx.link_secret, user.id, user.email
        )

    async def release_held(self, *, now: datetime | None = None) -> int:
        """Send what quiet hours parked, as ONE digest per user and channel, for every user whose
        window has closed. Returns the number of parked notifications released."""
        if not self._ctx.leader.is_leader():
            return 0
        now = now or datetime.now(UTC)
        released = 0
        async with self._ctx.session_factory() as session:
            store = SqlAlchemySchedulerStore(session)
            held = await store.list_held_notifications(since=now - _HELD_LOOKBACK)
            by_user: dict[str, list[Notification]] = {}
            for item in held:
                by_user.setdefault(item.user_id, []).append(item)
            for user_id, items in by_user.items():
                prefs = await store.get_preferences(user_id=user_id)
                if prefs is not None and _within_quiet_hours(
                    prefs.quiet_hours, now, prefs.timezone
                ):
                    continue  # the user's window is still open
                try:
                    user = await self._load_user(session, user_id)
                    if user is None:
                        continue
                    queued = await self._queue_held_digests(store, user, items)
                    released += await store.release_held_notifications(
                        ids=tuple(i.id for i in items)
                    )
                    # The digest rows are durable and the held ones released BEFORE anything is
                    # sent: a crash leaves pending rows the retry sweep delivers, never a re-send.
                    await session.commit()
                    recipient = await self._recipient_for(store, user)
                    for record_id, channel, notification in queued:
                        await self._send_and_record(
                            store,
                            user_id=user.id,
                            record_id=record_id,
                            channel=channel,
                            notification=notification,
                            recipient=recipient,
                            attempts=0,
                            track=True,
                        )
                except Exception:
                    await session.rollback()
                    logger.exception("held_release_failed", user_id=user_id)
        return released

    async def _queue_held_digests(
        self, store: SqlAlchemySchedulerStore, user: User, items: Sequence[Notification]
    ) -> list[tuple[str, DeliveryChannel, DeliveryNotification]]:
        """One digest per channel for the parked `items`, recorded as pending rows (so a retry
        resends the same layout) and returned, with the channel to send each on."""
        by_channel: dict[DeliveryChannelKind, list[Notification]] = {}
        for item in items:
            by_channel.setdefault(item.channel, []).append(item)
        queued: list[tuple[str, DeliveryChannel, DeliveryNotification]] = []
        for kind, group in by_channel.items():
            channel = self._ctx.channels.get(kind)
            if channel is None:
                continue
            titles = [str(g.payload.get("title") or "通知") for g in group]
            # Each stored body already ends in the footer; join them bare and add it once.
            lines = [
                f"{title}：{str(g.payload.get('body', '')).replace(NON_ADVICE_FOOTER, '').strip()}"
                for title, g in zip(titles, group, strict=True)
            ]
            joined = lines[0] if len(group) == 1 else "\n".join(f"・{line}" for line in lines)
            title = titles[0] if len(group) == 1 else f"靜音期間的通知（{len(group)} 則）"
            deep_link: dict[str, object] = {"route": "notifications"}
            layout = _scanned_layout(held_layout(titles))
            record = await store.record_notification(
                user_id=user.id,
                source=NotificationSource.DIGEST,
                source_id=f"held:{group[-1].id}",
                channel=kind,
                trigger_date=datetime.now(UTC).date(),
                payload=_notification_payload(
                    title,
                    joined,
                    deep_link,
                    held=False,
                    layout=layout if kind in _LAYOUT_CHANNELS else None,
                ),
                delivery_state="pending",
            )
            if record is None:
                continue
            notification = DeliveryNotification(
                notification_id=record.id,
                title=title,
                body=_external_body(joined),
                deep_link=deep_link,
                layout=layout,
            )
            queued.append((record.id, channel, notification))
        return queued

    async def _prune_stale(
        self, store: SqlAlchemySchedulerStore, user_id: str, receipt: DeliveryReceipt
    ) -> None:
        for token in receipt.stale_tokens:
            await store.unregister_device(user_id=user_id, token=token)
        if receipt.address_gone:
            await store.unlink_channel(user_id=user_id, channel=receipt.channel.value)
            logger.info("delivery_channel_unlinked", channel=receipt.channel.value, user_id=user_id)

    def _log_receipt(self, receipt: DeliveryReceipt) -> None:
        logger.info(
            "delivery_receipt",
            channel=receipt.channel.value,
            delivered=receipt.delivered,
            retryable=receipt.retryable,
        )

    async def _byok_for(self, session: AsyncSession, user: User) -> ByokOverride | None:
        """The owner's own key and default model for this run, or None to use the platform's."""
        if self._ctx.settings is None:
            return None
        # A schedule job carries no model of its own and has no thread, so the owner's saved
        # default (still catalogued, provider still keyed) is the only pick; otherwise the
        # provider's default_main. Never the platform's role map.
        return await resolve_byok_override(
            session,
            self._ctx.settings,
            user_id=user.id,
            model_override=None,
            default_model=user.default_model,
        )

    @staticmethod
    async def _record_usage(
        session: AsyncSession,
        *,
        user_id: str,
        run_id: str,
        result: OrchestratorResult,
        byok: bool,
    ) -> None:
        """Write the run's model usage to the same ledger interactive turns use, so scheduled
        runs show on the owner's usage page. One id per run (a job fires many times). Best-effort:
        a failed write is rolled back and logged, never failing the run."""
        usage = result.usage
        if usage is None:
            return
        try:
            await write_turn_usage(
                session,
                user_id=user_id,
                turn_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                tool_calls=usage.tool_calls,
                finmind_calls=usage.finmind_calls,
                cost_micros=usage.cost_micros,
            )
            await write_model_usage(
                session, user_id=user_id, turn_id=run_id, models=usage.models, byok=byok
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("schedule_usage_record_failed", run_id=run_id, error=repr(exc))

    async def _load_user(self, session: AsyncSession, user_id: str) -> User | None:
        try:
            return await SqlAlchemyUserStore(session).get(user_id=user_id)
        except UserNotFoundError:
            return None

    async def _persist_turn(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        text: str,
        cards: Sequence[RichCard],
        job: ScheduleJob,
    ) -> str:
        """Append the scheduled brief to the schedule's ONE persistent thread (created on the first
        run), so a daily briefing reads as a single running conversation instead of a new thread
        per run. If the owner deleted that thread, the next run starts a fresh one. Returns the
        thread id the brief landed in."""
        return await self._append_to_thread(
            session,
            user_id=user_id,
            thread_id=job.thread_id,
            title="排程摘要",
            text=text,
            cards=cards,
            caveat="排程觀察，非投資建議。",
        )

    async def _append_to_thread(  # noqa: PLR0913 — one keyword per thing a scheduled turn writes
        self,
        session: AsyncSession,
        *,
        user_id: str,
        thread_id: str | None,
        title: str,
        text: str,
        cards: Sequence[RichCard],
        caveat: str,
    ) -> str:
        store = SqlAlchemyConversationStore(session)
        parent_message_id: str | None = None
        if thread_id is not None:
            try:
                path = await store.active_path(thread_id=thread_id)
            except ThreadNotFoundError:
                thread_id = None
            else:
                parent_message_id = path[-1].id if path else None
        if thread_id is None:
            thread_id = (await store.create_thread(user_id=user_id, title=title)).id
        await store.append_turn(
            user_id=user_id,
            thread_id=thread_id,
            role=TurnRole.ASSISTANT,
            # The chart/table cards the skill's tools produced go first, exactly as in an
            # interactive answer — a scheduled report without its charts would be the text alone.
            blocks=[
                *(
                    (
                        MessageBlockType.RICH_CARD,
                        RichCardBlock(card_type=card.card_type, data=dict(card.data)),
                    )
                    for card in cards
                ),
                (MessageBlockType.TEXT, TextBlock(text=text)),
                (
                    MessageBlockType.CAVEAT,
                    CaveatBlock(kind="scheduled", text=caveat),
                ),
            ],
            parent_message_id=parent_message_id,
        )
        return thread_id

    def _emit(self, kind: EventKind, *, user_id: str, data: dict[str, object]) -> None:
        if self._ctx.event_bus is not None:
            self._ctx.event_bus.publish(kind, data={"user_id": user_id, **data})

    async def _nothing_to_review(
        self,
        store: SqlAlchemySchedulerStore,
        session: AsyncSession,
        job: ScheduleJob,
        user: User,
        now: datetime,
    ) -> bool:
        """The weekly watchlist review of a user with no watchlist symbol at all: a calm one-line
        notice, the cadence moves on, and the job is OK - an empty list is a normal state, not an
        error. Checked before the quota gate and the model run, so it spends no chat."""
        if not is_weekly_review(job.action.skill_ref, job.action.inputs):
            return False
        symbols = await store.brief_symbols(user_id=user.id)
        if symbols.tw_stocks or symbols.us_stocks or symbols.etfs:
            return False
        await self._deliver(
            store,
            user=user,
            source=NotificationSource.SCHEDULE,
            source_id=job.id,
            channels=job.delivery_channels,
            title=schedule_label(job.action.skill_ref),
            body=NOTHING_TO_REVIEW,
            trigger_date=now.date(),
            deep_link={"route": "notifications"},
        )
        await store.set_job_status(
            job_id=job.id,
            status=JobStatus.OK,
            last_run=now,
            next_run=self._next_run(job, now),
            reason="nothing_to_review",
        )
        await session.commit()
        self._emit(EventKind.SCHEDULE_SKIPPED, user_id=user.id, data={"job_id": job.id})
        logger.info("schedule_job_nothing_to_review", job_id=job.id)
        return True

    async def _resolved_inputs(
        self, store: SqlAlchemySchedulerStore, job: ScheduleJob
    ) -> dict[str, object] | None:
        """The skill inputs for this run. A schedule scoped to a watchlist stores only the list's
        id; the Taiwan members are read fresh each run, so adding a stock to the list changes the
        next briefing without editing the schedule. `None` means the list has nothing to brief."""
        inputs = dict(job.action.inputs)
        watchlist_id = inputs.pop(WATCHLIST_INPUT, None)
        if not isinstance(watchlist_id, str) or not watchlist_id:
            return inputs
        members = await store.watchlist_members(watchlist_id=watchlist_id, user_id=job.user_id)
        stock_ids = [symbol for symbol, market in members if market is Market.TW][:MAX_TARGETS]
        if not stock_ids:
            return None
        inputs["stock_ids"] = stock_ids
        return inputs

    def _job_message(self, job: ScheduleJob, inputs: dict[str, object] | None = None) -> str:
        """The user turn a scheduled run hands the skill. Skills name their target input
        differently (`stock_ids`, `stock_id`, `etf_id`, `symbol_id`...), so every input is spelled
        out instead of guessing one key: a run that silently dropped its target would brief on
        nothing."""
        skill = job.action.skill_ref
        given = job.action.inputs if inputs is None else inputs
        inputs = {k: v for k, v in given.items() if v not in (None, "", [], ())}
        if not inputs:
            return f"排程執行技能 {skill}"
        detail = "、".join(f"{key}={value}" for key, value in inputs.items())
        return f"排程執行技能 {skill}，參數：{detail}"

    def _daily_limit_reached(self, delivered_today: int, daily_limit: int | None) -> bool:
        return daily_limit is not None and delivered_today >= daily_limit

    def _next_run(self, job: ScheduleJob, now: datetime) -> datetime | None:
        """The job's next concrete fire time from its trigger. `None` (left unset) if the trigger
        can no longer be resolved — the job then simply stops being due rather than looping."""
        try:
            return next_run_after(job.trigger, now)
        except UnresolvableTriggerError:
            logger.warning("schedule_job_unresolvable_trigger", job_id=job.id)
            return None


def _delivery_outcome(
    receipt: DeliveryReceipt, attempts: int, now: datetime
) -> tuple[str, datetime | None]:
    """(state, next attempt time) after one send. A delivered notification is done; a failure that
    is not transient, or that has used up its attempts, is final; any other failure is retried
    after a delay that doubles each time, from 5 minutes up to 6 hours."""
    if receipt.delivered:
        return "sent", None
    if not receipt.retryable or attempts + 1 >= _MAX_DELIVERY_ATTEMPTS:
        return "failed", None
    delay = min(_RETRY_BASE_DELAY * (2**attempts), _RETRY_MAX_DELAY)
    return "pending", now + delay


def _digest_day(prefs: AlertPreference | None, now: datetime) -> date | None:
    """The user's local date if a digest is due right now, else None: inside the 08:00-12:00 window
    of a Taiwan trading day, and not while their quiet hours are running."""
    if prefs is None:
        return None
    local = as_utc(now).astimezone(_user_zone(prefs.timezone))
    if not (_DIGEST_FROM <= local.time() < _DIGEST_UNTIL):
        return None
    if not is_market_open_day(local.date(), Market.TW):
        return None
    if _within_quiet_hours(prefs.quiet_hours, now, prefs.timezone):
        return None
    return local.date()


def _external_body(body: str) -> str:
    """What leaves the building on an external channel: the body with the non-advice footer (once),
    passed through the same output scan an interactive answer gets. Scheduled-report previews and
    held digests reach this path without having gone through `safe_alert_message`, so the
    guarantee is made here, where every external send passes. Fails closed to a fixed line if the
    scan trips."""
    text = body if NON_ADVICE_FOOTER in body else f"{body}\n{NON_ADVICE_FOOTER}"
    if check_output(text, has_market_data=False).reason == "trading_language_redacted":
        return f"通知內容已更新，請開啟 Miru 查看。\n{NON_ADVICE_FOOTER}"
    return text


def as_utc(moment: datetime) -> datetime:
    """A stored timestamp as an aware UTC datetime. `DateTime(timezone=True)` round-trips as a
    NAIVE value on SQLite (dev) and an aware one on Postgres (prod); every stored timestamp is
    UTC, so a naive one is labelled UTC. `naive.astimezone(UTC)` would instead assume the host's
    local zone and shift the value by its offset."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


async def _subscription_members(
    store: SqlAlchemySchedulerStore, subscription: StrategySubscription, user_id: str
) -> frozenset[str] | None:
    """The Taiwan symbols of the subscription's watchlist, or None when it covers everything."""
    if subscription.watchlist_id is None:
        return None
    group = await store.watchlist_members(watchlist_id=subscription.watchlist_id, user_id=user_id)
    return frozenset(symbol for symbol, market in group if market is Market.TW)


async def _fail_unsendable(store: SqlAlchemySchedulerStore, notification_id: str) -> None:
    await store.mark_delivery(
        notification_id=notification_id, state="failed", attempts=0, next_attempt_at=None
    )
    await store.session.commit()


def _channel_order(channels: tuple[DeliveryChannelKind, ...]) -> tuple[DeliveryChannelKind, ...]:
    """The in-app inbox entry always comes first (it is the dedupe guard), then the others."""
    return (
        DeliveryChannelKind.IN_APP,
        *(c for c in channels if c is not DeliveryChannelKind.IN_APP),
    )


def _notification_payload(
    title: str,
    body: str,
    deep_link: dict[str, object],
    *,
    held: bool,
    layout: Layout | None = None,
) -> dict[str, object]:
    return {
        "title": title,
        "body": body,
        "deep_link": deep_link,
        **({"held": True} if held else {}),
        **({"layout": layout.model_dump(mode="json")} if layout is not None else {}),
    }


def _scanned_layout(layout: Layout | None) -> Layout | None:
    """`layout`, cut to the size a stored row keeps, if the text it renders passes the output scan,
    else None (plain text is sent)."""
    if layout is None:
        return None
    bounded = trim_layout(layout, NON_ADVICE_FOOTER)
    if bounded is None:
        return None
    clean = check_output(render_text(bounded, NON_ADVICE_FOOTER), has_market_data=False).reason
    return bounded if clean is None else None


def _met_map(state: dict[str, object]) -> dict[str, bool]:
    raw_met = state.get("met")
    if not isinstance(raw_met, dict):
        return {}
    return {str(k): bool(v) for k, v in cast("dict[object, object]", raw_met).items()}


def _should_fire(
    rule: AlertRule,
    evaluation: Evaluation,
    *,
    was_met: bool,
    state: dict[str, object],
    now: datetime,
) -> bool:
    """Whether one met evaluation notifies: edge-triggering (transition only, except event kinds)
    and the cooldown both suppress it."""
    if not evaluation.met:
        return False
    edge = rule.trigger_mode is not TriggerMode.EVERY_OCCURRENCE
    if edge and was_met and rule.condition.kind not in EVENT_KINDS:
        return False  # still true since the last evaluation — fire on the transition only
    return not _in_cooldown(rule, state, now)


async def _delivery_settings(
    store: SqlAlchemySchedulerStore,
    *,
    user_id: str,
    channels: tuple[DeliveryChannelKind, ...],
    quiet: QuietHours | None,
    at: datetime,
) -> tuple[tuple[DeliveryChannelKind, ...], bool]:
    """The channels and quiet-hours hold for one rule or subscription: its own override, else the
    user's saved preferences."""
    prefs = await store.get_preferences(user_id=user_id)
    resolved_channels = channels or (prefs.channels if prefs else ())
    resolved_quiet = quiet or (prefs.quiet_hours if prefs else None)
    return resolved_channels, _within_quiet_hours(
        resolved_quiet, at, prefs.timezone if prefs else None
    )


def _as_int(raw: object) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0


def _in_cooldown(rule: AlertRule, state: dict[str, object], now: datetime) -> bool:
    raw = state.get("last_fired_at")
    if rule.cooldown_seconds is None or not isinstance(raw, str):
        return False
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (now - last).total_seconds() < rule.cooldown_seconds


def _compute_evaluations(
    rules: list[AlertRule],
    targets: dict[str, list[tuple[str, Market]]],
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> dict[str, dict[str, Evaluation]]:
    """Blocking: read the partitions and evaluate every `(rule, symbol)` for this market. Run in a
    thread by the caller. Symbols with no data for `as_of` are simply absent from the result."""
    out: dict[str, dict[str, Evaluation]] = {}
    for rule in rules:
        per_symbol: dict[str, Evaluation] = {}
        for symbol, target_market in targets.get(rule.id, []):
            if target_market is not market:
                continue
            evaluation = evaluate_condition(
                rule.condition, symbol=symbol, market=market, as_of=as_of, view=view
            )
            if evaluation is not None:
                per_symbol[symbol] = evaluation
        out[rule.id] = per_symbol
    return out


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
    reader: SnapshotReader | None = None,
    brief_tool: Tool | None = None,
    public_base_url: str | None = None,
    link_secret: str | None = None,
    app_base_url: str | None = None,
    settings: Settings | None = None,
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
            reader=reader,
            brief_tool=brief_tool,
            public_base_url=public_base_url,
            link_secret=link_secret,
            app_base_url=app_base_url,
            settings=settings,
        )
    )


__all__ = [
    "SchedulerContext",
    "SchedulerWorker",
    "build_scheduler_worker",
]
