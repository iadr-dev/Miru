"""The standalone scheduler process — `python -m scheduler`.

Turns the library-level `SchedulerWorker` (a single pass over due jobs / enabled alerts) into a
long-running daemon: wake on a config-driven interval, fire every due job and evaluate every
enabled alert, sleep, repeat. Leadership is checked inside each pass (a non-leader pass is a silent
no-op), so a standby replica loops harmlessly until it wins the election.

Graceful shutdown (SIGTERM/SIGINT): a scheduled run is a short-lived read (fetch official/EOD data,
render a brief, deliver) — not a long-lived SSE turn — so the API worker's SSE-draining shutdown is
scoped down here to the essentials that still matter: on the first signal, stop starting new passes
immediately, but let a pass already in flight finish so a job that has begun firing completes its
deliver-and-record step (which is what keeps at-least-once delivery idempotent rather than leaving a
half-written run). A second signal escalates to an immediate stop. There is no partial-turn
checkpoint to emit because a scheduled pass either completed a job's delivery (recorded and
dedupe-safe) or hadn't started it, so a clean stop between jobs loses no durable state.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from config.logging_config import configure_logging
from config.settings import get_settings
from db import rls
from scheduler.composition import build_alert_update_source, build_scheduler_from_settings
from scheduler.subscriber import run_subscriber
from scheduler.worker import SchedulerWorker

logger = structlog.get_logger(__name__)


class _ShutdownState:
    """Tracks the cooperative-stop request set by the signal handlers."""

    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self.hard_stop = asyncio.Event()

    def request(self) -> None:
        if self.stop.is_set():
            # A second signal — escalate: the operator asked twice, don't wait out the in-flight
            # pass. The run loop cancels its current sleep/pass and exits.
            self.hard_stop.set()
            logger.warning("scheduler_shutdown_escalated")
            return
        self.stop.set()
        logger.info("scheduler_shutdown_requested")


async def _run_once(worker: SchedulerWorker) -> None:
    """One full pass: fire due jobs, then evaluate enabled alerts. Never lets one failing pass kill
    the daemon — a pass-level error is logged and the loop continues to the next interval, because a
    scheduler that crashes on a transient store hiccup is worse than one that retries next tick."""
    try:
        ran = await worker.run_due_jobs()
        fired = await worker.reconcile_alerts()
        released = await worker.release_held()
        digests = await worker.send_morning_digests()
        logger.info(
            "scheduler_pass_complete",
            jobs_run=len(ran),
            alerts_fired=len(fired),
            held_released=released,
            digests_sent=digests,
        )
    except Exception:
        logger.exception("scheduler_pass_failed")


async def run_scheduler_loop(
    worker: SchedulerWorker,
    *,
    interval_seconds: float,
    shutdown: _ShutdownState,
) -> None:
    """Loop `_run_once` on `interval_seconds` until a stop is requested, letting an in-flight pass
    finish on a graceful stop and abandoning it immediately on a hard (second-signal) stop."""
    while not shutdown.stop.is_set():
        # Run the pass as a task so a hard stop (second signal) can cancel it mid-flight; a graceful
        # first stop leaves it to finish — a job that has begun firing completes its record-and-
        # deliver step, keeping at-least-once delivery dedupe-safe rather than half-written.
        pass_task = asyncio.ensure_future(_run_once(worker))
        hard = asyncio.ensure_future(shutdown.hard_stop.wait())
        done, _pending = await asyncio.wait({pass_task, hard}, return_when=asyncio.FIRST_COMPLETED)
        if pass_task not in done:
            pass_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pass_task
            logger.warning("scheduler_pass_cancelled_on_hard_stop")
        hard.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hard
        if shutdown.stop.is_set():
            break
        # Sleep the interval, but wake early if a shutdown lands so we don't idle for a full
        # interval after being told to stop.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.stop.wait(), timeout=interval_seconds)
    logger.info("scheduler_loop_exited")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    rls.install(enabled=settings.row_level_security)
    # This process works across all users (due jobs, alert sweeps): it is system work, not one user.
    rls.enter_system_mode()
    worker = await build_scheduler_from_settings(settings)
    shutdown = _ShutdownState()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # add_signal_handler is unavailable on some platforms (e.g. Windows ProactorEventLoop);
            # there the KeyboardInterrupt path in `__main__` still stops the process cleanly.
            loop.add_signal_handler(sig, shutdown.request)

    logger.info(
        "scheduler_process_start",
        interval_seconds=settings.scheduler_poll_interval_seconds,
    )
    update_source = build_alert_update_source(settings)
    subscriber: asyncio.Task[None] | None = None
    if update_source is not None:
        subscriber = asyncio.create_task(
            run_subscriber(worker, update_source, stop=shutdown.stop), name="alert-subscriber"
        )
    else:
        logger.info("alert_subscriber_disabled", reason="event bus is not redis; sweep only")
    try:
        await run_scheduler_loop(
            worker,
            interval_seconds=settings.scheduler_poll_interval_seconds,
            shutdown=shutdown,
        )
    finally:
        if subscriber is not None:
            subscriber.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await subscriber


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
