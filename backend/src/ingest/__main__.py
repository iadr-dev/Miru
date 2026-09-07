"""The standalone platform-ingest process — `python -m ingest`.

Turns the library-level `IngestDaemon` (one pass over the day's due whole-market pulls) into a
long-running daemon: optionally self-heal any gap since the last run, seed the startup state so an
already-elapsed window isn't replayed, then wake on a config-driven interval, fire whatever became
due, sleep, repeat. Leadership is checked inside each pass (a non-leader pass is a silent no-op),
so a standby replica loops harmlessly until it wins the election.

**Startup gap-fill (`INGEST_ON_STARTUP`, dev-only).** A dev stack is not continuously up the way
prod is — the box sleeps, `docker compose down`/`up` happens between sessions, a rebuild restarts
the container — and `IngestDaemon.seed_startup()` deliberately does NOT replay a window that
elapsed while the process was down (see its own docstring: "production boots clean"). Left alone,
that means a dev stack that was down across a job's fire time permanently skips that day with no
automatic catch-up. When `settings.ingest_on_startup` is true, `main()` runs the same one-shot
gap-fill `ingest.backfill_cli`'s operator CLI uses (`run_backfill_cli`/`run_us_backfill_cli`,
`only_missing=True` under the hood) once before entering the poll loop — every `docker compose up`
self-heals whatever gap accumulated, with no flag to remember. `run_backfill_cli` itself hard-stops
if `settings.env == "production"` regardless of this flag, so a mis-set env var can never turn
into an accidental prod backfill burst.

`_tw_gap_fill` also directly re-runs the two MOPS whole-market jobs
(`job_material_announcements`/`job_insider_transfer_declaration`) AND the two 處置王-board
whole-market jobs (`job_attention_stocks`/`job_disposition_events`) for today, the same
direct-call treatment `_us_gap_fill` already gives `job_us_fundamentals` — none of these four are
a `WholeMarketJobSpec`, so `run_backfill_cli`'s own gap-fill loop never reaches them. This matters
more for these four: their upstreams have no date parameter at all, so a day missed at the
daemon's own cron fire time (`ingest/daemon.py`'s `21:35` slot) cannot be recovered any other way
— there is nothing upstream to backfill from later.

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
from datetime import UTC, datetime

import structlog

from config.settings import Settings, get_settings
from ingest.backfill_cli import (
    run_backfill_cli,
    run_derived_compute_catchup,
    run_us_backfill_cli,
)
from ingest.context import IngestContext
from ingest.daemon import IngestDaemon, build_ingest_context_from_settings
from ingest.jobs import (
    job_attention_stocks,
    job_disposition_events,
    job_insider_transfer_declaration,
    job_material_announcements,
)
from ingest.us_jobs import job_us_fundamentals

logger = structlog.get_logger(__name__)


async def _tw_gap_fill(
    settings: Settings, ctx: IngestContext
) -> tuple[int, int, str, str, str, str]:
    """The TW chain: whole-market pulls, THEN derived-compute — sequential because
    `screener_flags`/`stock_scores` read back the price/flow partitions the whole-market pulls
    just wrote, so derived-compute for a given day cannot run before that day's own TW data has
    landed.

    Also runs the two MOPS whole-market jobs (`job_material_announcements`/
    `job_insider_transfer_declaration`) AND the two 處置王-board whole-market jobs
    (`job_attention_stocks`/`job_disposition_events`) directly for today, mirroring
    `_us_gap_fill`'s own `job_us_fundamentals(datetime.now(UTC).date(), ctx)` precedent — none of
    these four are `WholeMarketJobSpec`-shaped, so `run_backfill_cli`'s own per-spec gap-fill
    above never reaches them, same class of gap `job_us_fundamentals` needed this treatment for.
    It matters MORE for these four than for `job_us_fundamentals`: their upstreams are
    snapshot-only with no backfill possible at all (see `job_material_announcements`'s own
    docstring) — a day missed because the container was down at the cron fire time
    (`ingest/daemon.py`'s `21:35` slot) is otherwise permanently, unrecoverably lost. Idempotent
    (`write_partition` always rewrites the whole date partition), so calling these unconditionally
    here is always safe even if today's cron slot already ran this session."""
    tw_runs = await run_backfill_cli(
        settings, ctx, override=False, years=settings.dev_backfill_years
    )
    # Derived-compute (`stock_scores`/`screener_flags`) is not a `WholeMarketJobSpec`, so
    # `run_backfill_cli`'s per-spec gap-fill above never reaches it — see this module's own
    # docstring for why a dev restart otherwise leaves those two permanently stuck.
    derived_runs = await run_derived_compute_catchup(
        settings, ctx, override=False, years=settings.dev_backfill_years
    )
    today = datetime.now(UTC).date()
    announcements_run = await job_material_announcements(today, ctx)
    insider_transfer_run = await job_insider_transfer_declaration(today, ctx)
    attention_stocks_run = await job_attention_stocks(today, ctx)
    disposition_events_run = await job_disposition_events(today, ctx)
    return (
        len(tw_runs),
        len(derived_runs),
        announcements_run.status.value,
        insider_transfer_run.status.value,
        attention_stocks_run.status.value,
        disposition_events_run.status.value,
    )


async def _us_gap_fill(settings: Settings, ctx: IngestContext) -> tuple[int, str]:
    """The US branch: price backfill, then a fundamentals catch-up pull for today.

    `job_us_fundamentals` is otherwise only reachable via its own WEEKLY cron slot
    (`ingest/daemon.py`'s `SCHEDULED_JOBS`) — a dev restart that lands between two weekly fire
    windows leaves it stuck until the next one, same class of gap `_tw_gap_fill`'s derived-compute
    catchup exists to close. Unlike price, fundamentals has no meaningful "backfill a date range"
    operation (yfinance's financials/dividends/splits endpoints return each symbol's own
    available history, not a caller-scoped range — see `run_us_backfill_cli`'s own docstring for
    why it deliberately excludes this job) — so this is a single current-snapshot pull for
    today, not a loop over a window."""
    price_runs = await run_us_backfill_cli(
        settings, ctx, override=False, years=settings.dev_backfill_years
    )
    fundamentals_run = await job_us_fundamentals(datetime.now(UTC).date(), ctx)
    return len(price_runs), fundamentals_run.status.value


async def _startup_gap_fill(settings: Settings, ctx: IngestContext) -> None:
    """Best-effort catch-up before the poll loop starts — never fatal: a failure here (e.g. a
    transient FinMind/yfinance outage) must not prevent the daemon from starting its normal
    schedule, since the poll loop's own daily jobs will simply try again at their next fire time
    regardless. Skipped entirely (and logged as such) when `ingest_on_startup` is false — the
    production default, and this function's only real gate.

    The TW chain (`_tw_gap_fill`) and the US chain (`_us_gap_fill`) run CONCURRENTLY via
    `asyncio.gather` — they touch entirely disjoint datasets/sources (FinMind vs yfinance) with no
    ordering dependency on each other. Note this concurrency is real only at each branch's own
    `await` points: `_tw_gap_fill`'s derived-compute (`job_scoring_batch`/`job_screener_flags`) is
    CPU-bound pandas/stats work with almost no `await`s of its own, so it still blocks the event
    loop — and therefore the US branch too — for seconds at a stretch between its own awaits. This
    still nets out ahead of pure sequential execution (the US branch's own awaits interleave
    whenever the TW branch does yield), but does not guarantee the US branch makes steady progress
    while a single TW compute call is mid-flight."""
    if not settings.ingest_on_startup:
        return
    logger.info("ingest_startup_gap_fill_started")
    try:
        (
            (
                tw_runs,
                derived_runs,
                announcements_status,
                insider_transfer_status,
                attention_stocks_status,
                disposition_events_status,
            ),
            (us_price_runs, fundamentals_status),
        ) = await asyncio.gather(
            _tw_gap_fill(settings, ctx),
            _us_gap_fill(settings, ctx),
        )
    except Exception:
        logger.exception("ingest_startup_gap_fill_failed")
        return
    logger.info(
        "ingest_startup_gap_fill_complete",
        tw_runs=tw_runs,
        derived_runs=derived_runs,
        mops_announcements_status=announcements_status,
        mops_insider_transfer_status=insider_transfer_status,
        attention_stocks_status=attention_stocks_status,
        disposition_events_status=disposition_events_status,
        us_price_runs=us_price_runs,
        us_fundamentals_status=fundamentals_status,
    )


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
    ctx = await build_ingest_context_from_settings(settings)
    daemon = IngestDaemon(ctx)
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
    # The startup gap-fill and the normal poll loop run CONCURRENTLY, not sequentially: a
    # multi-year historical gap-fill can take hours, and awaiting it before the poll loop even
    # starts would delay every WEEKLY/MONTHLY cron job (e.g. `job_us_fundamentals`) from firing
    # at all until the whole gap-fill finishes — even though that cron job has no dependency on
    # the gap-fill completing. Both write to the store through the same atomic-partition-swap
    # path and are individually idempotent, so a gap-fill run and a live cron pass touching
    # overlapping dates is a same-answer overwrite, not a correctness risk — the same reasoning
    # `_startup_gap_fill`'s own TW/US concurrency already relies on.
    await asyncio.gather(
        _startup_gap_fill(settings, ctx),
        run_ingest_loop(
            daemon,
            interval_seconds=settings.ingest_poll_interval_seconds,
            shutdown=shutdown,
        ),
    )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
