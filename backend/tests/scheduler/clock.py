"""A pinned wall clock for `tests/scheduler/`.

The alert tests build "today" from the Taipei calendar and the code under test stamps rows with
`datetime.now(UTC)`. Reading the real clock for both made the suite depend on when it ran: a CI run
that collected at 15:59 UTC (Taipei 23:59) and ran its scheduler tests after 16:00 UTC (Taipei
00:00) saw the rule's `created_at` one Taipei day after the data's `as_of`, and every alert test
failed the "never fire on data that predates the rule" guard. The production code was right (it
compares in the market's timezone); the tests were reading two different instants.

Here "now" is one pinned instant, shared by the test constants (`TODAY`, `day(n)`) and, via the
`pinned_clock` fixture, by every `datetime.now(...)` in the `scheduler` and `events` packages and
in these tests. Override it with `MIRU_TEST_NOW` (an ISO-8601 instant) to run the whole directory
at another time of day, e.g. `MIRU_TEST_NOW=2026-10-26T16:05:00Z uv run pytest tests/scheduler`.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
# Tuesday 12:00 Taipei: a trading day, far from any day boundary, with trading days on both sides.
DEFAULT_NOW = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)


def initial() -> datetime:
    raw = os.environ.get("MIRU_TEST_NOW")
    if not raw:
        return DEFAULT_NOW
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_current: datetime = initial()
_REAL_DATETIME = datetime


def current() -> datetime:
    """The pinned instant, as an aware UTC datetime."""
    return _current


def set_now(moment: datetime) -> None:
    """Move the pinned clock (used by the boundary tests)."""
    global _current  # noqa: PLW0603 — the one pinned clock
    _current = moment.astimezone(UTC)


def taipei_today() -> date:
    return _current.astimezone(TAIPEI).date()


class _Meta(type):
    # Rows read back from SQLite are real `datetime`s; keep `isinstance(x, datetime)` true for them.
    def __instancecheck__(cls, instance: object) -> bool:
        return isinstance(instance, _REAL_DATETIME)

    def __subclasscheck__(cls, subclass: type) -> bool:
        return issubclass(subclass, _REAL_DATETIME)


class PinnedDateTime(_REAL_DATETIME, metaclass=_Meta):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return _current.astimezone(tz) if tz is not None else _current.replace(tzinfo=None)


_PATCHED_PREFIXES = ("scheduler", "events", "tests.scheduler")


def patched_modules() -> list[str]:
    """Loaded modules that read the wall clock through a module-level `datetime` name."""
    return [
        name
        for name, module in list(sys.modules.items())
        if name.startswith(_PATCHED_PREFIXES)
        and name != __name__
        and getattr(module, "datetime", None) is _REAL_DATETIME
    ]
