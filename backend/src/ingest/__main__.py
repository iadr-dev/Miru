"""The standalone platform-ingest process — `python -m ingest`.

Turns the library-level `IngestDaemon` (one pass over the day's due whole-market pulls) into a
long-running daemon: seed the startup state so an already-elapsed window isn't replayed, then wake
on a config-driven interval, fire whatever became due, sleep, repeat. Leadership is checked inside
each pass (a non-leader pass is a silent no-op), so a standby replica loops harmlessly until it
wins the election.

Graceful shutdown (SIGTERM/SIGINT): a whole-market pull is a bounded batch write (fetch one
dataset by date, bulk-COPY the partition atomically, flip the manifest pointer) — not an unbounded
stream. So the first signal stops starting new passes immediately but lets an in-flight pass finish
its atomic partition swap, which is what keeps the store's readers seeing only fully-written
partitions. A second signal escalates to an immediate stop; the atomic-swap writer leaves no
half-written partition even if cancelled mid-pass, and every job is idempotent, so a cancelled pass
simply re-runs next tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from config.settings import get_settings
from ingest.daemon import IngestDaemon, build_ingest_daemon_from_settings

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
            logger.warning("ingest_shutdown_escalated")
            return
        self.stop.set()
        logger.info("ingest_shutdown_requested")


async def _run_once(daemon: IngestDaemon) -> None:
    """One pass over the day's due pulls. Never lets one failing pass kill the daemon — a
    pass-level error is logged and the loop continues to the next interval."""
    try:
        ran = await daemon.run_due_jobs()
        logger.info("ingest_pass_complete", jobs_run=len(ran))
    except Exception:
        logger.exception("ingest_pass_failed")


async def run_ingest_loop(
    daemon: IngestDaemon,
    *,
    interval_seconds: float,
    shutdown: _ShutdownState,
) -> None:
    """Loop `_run_once` on `interval_seconds` until a stop is requested, letting an in-flight pass
    finish on a graceful stop and abandoning it immediately on a hard (second-signal) stop."""
    while not shutdown.stop.is_set():
        # Run the pass as a task so a hard stop (second signal) can cancel it mid-flight; a graceful
        # first stop leaves it to finish its atomic partition swap rather than tearing a write.
        pass_task = asyncio.ensure_future(_run_once(daemon))
        hard = asyncio.ensure_future(shutdown.hard_stop.wait())
        done, _pending = await asyncio.wait({pass_task, hard}, return_when=asyncio.FIRST_COMPLETED)
        if pass_task not in done:
            pass_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pass_task
            logger.warning("ingest_pass_cancelled_on_hard_stop")
        hard.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hard
        if shutdown.stop.is_set():
            break
        # Sleep the interval, but wake early if a shutdown lands so we don't idle for a full
        # interval after being told to stop.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.stop.wait(), timeout=interval_seconds)
    logger.info("ingest_loop_exited")


async def main() -> None:
    settings = get_settings()
    daemon = await build_ingest_daemon_from_settings(settings)
    daemon.seed_startup()
    shutdown = _ShutdownState()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # add_signal_handler is unavailable on some platforms (e.g. Windows ProactorEventLoop);
            # there the KeyboardInterrupt path in `__main__` still stops the process cleanly.
            loop.add_signal_handler(sig, shutdown.request)

    logger.info(
        "ingest_process_start",
        interval_seconds=settings.ingest_poll_interval_seconds,
    )
    await run_ingest_loop(
        daemon,
        interval_seconds=settings.ingest_poll_interval_seconds,
        shutdown=shutdown,
    )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
