"""The auditable ingest journal — every run must be journaled with dataset, date, rows, and
status, so that gaps in the historical store are visible rather than silent.

**Sink choice for this phase: structured logs (structlog), not a DB table.** A dedicated
`ingest_runs` table with query/alerting on top belongs to a later phase (the interaction-layer
event/journal system, and the scheduler's own run-history store) — building it here would be
scope creep. What's required is that *every run is journaled* with
`(dataset, date, rows, status, timestamp)`; `JobRun` + `journal_run()` below satisfy that today
via one structured log line per run, and are deliberately shaped (typed fields, not a
free-text message) so a future DB-backed sink can be swapped in by changing `journal_run()`'s
body alone — no call-site changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"  # calendar-gated no-op, or leadership not held


@dataclass(frozen=True)
class JobRun:
    """One journaled ingest run — the mandated audit record."""

    dataset: str
    run_date: date_
    rows_written: int
    status: RunStatus
    detail: str = ""
    journaled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # SKIPPED because the dataset is not in the FinMind plan (and no free source serves it):
    # reported apart from the other skips, and never as a failure.
    tier_skipped: bool = False


def journal_run(
    dataset: str,
    run_date: date_,
    *,
    rows_written: int,
    status: RunStatus,
    detail: str = "",
) -> JobRun:
    """Record one ingest run. Emits a single structured log line — never logs the FinMind
    token or any raw upstream payload, only the required audit fields."""
    return _record(
        JobRun(
            dataset=dataset,
            run_date=run_date,
            rows_written=rows_written,
            status=status,
            detail=detail,
            journaled_at=datetime.now(UTC),
        )
    )


def journal_tier_skip(dataset: str, run_date: date_, detail: str) -> JobRun:
    """Record a run skipped because the dataset is not in the FinMind plan (nothing else serves
    it): SKIPPED, flagged so a summary can count it apart from every other skip."""
    return _record(
        JobRun(
            dataset=dataset,
            run_date=run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=detail,
            journaled_at=datetime.now(UTC),
            tier_skipped=True,
        )
    )


def _record(run: JobRun) -> JobRun:
    logger.info(
        "ingest_run_journaled",
        dataset=run.dataset,
        run_date=run.run_date.isoformat(),
        rows_written=run.rows_written,
        status=run.status.value,
        detail=run.detail,
        tier_skipped=run.tier_skipped,
    )
    return run


__all__ = ["JobRun", "RunStatus", "journal_run", "journal_tier_skip"]
