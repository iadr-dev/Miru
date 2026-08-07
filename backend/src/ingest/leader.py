"""Leader-election seam — a single leader prevents double-writes; only the leader writes
Parquet, and every other worker stays read-only.

**This is the ingest-local version; a future infrastructure phase will generalize this** into
a real cross-process election (Postgres advisory lock or a broker-based lease). What matters
for *this* phase is that every write-path caller in `ingest/` asks "am I the leader?" before
writing — never silently assumes single-process forever — so swapping in the real
implementation later is a one-line change at the call site, not a redesign.
"""

from __future__ import annotations

from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)


class LeaderElection(Protocol):
    """Answers "is this process currently the ingest leader?" The sole writer of the derived
    Parquet store must hold leadership for the duration of a write."""

    def is_leader(self) -> bool:
        """True if this process may write. Never blocks; a `False` result means "skip this
        write, someone else / nothing owns it right now" — never a hard failure."""
        ...


class AlwaysLeader:
    """Dev/single-process default: this process is always the leader.

    Correct for local dev and for a single scheduler-worker deployment; **not** correct once
    ≥2 scheduler workers run concurrently (a future infrastructure phase must swap this for a
    real election before that topology ships — a locking job store so multiple workers run
    safely without double-firing). Every ingest write path takes a `LeaderElection` instance
    rather than hardcoding "always write", so that swap touches only the construction site
    (e.g. `config/` wiring), never the write-path logic itself.
    """

    def is_leader(self) -> bool:
        return True


class NeverLeader:
    """Test/standby double: always reports non-leadership, so callers can assert their
    write path correctly no-ops when leadership is lost — the shape a real standby-follower
    would take once a real election implementation lands."""

    def is_leader(self) -> bool:
        return False


def get_default_leader_election() -> LeaderElection:
    """The ingest-local default wiring: single-process dev/staging assumes leadership.
    Replace this factory (not call sites) when a real election implementation lands."""
    return AlwaysLeader()


__all__ = [
    "AlwaysLeader",
    "LeaderElection",
    "NeverLeader",
    "get_default_leader_election",
]
