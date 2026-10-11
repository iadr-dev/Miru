"""Regression guard for a second, narrower collision `mcp._vendor.bootstrap_vendor_sdk` must
also guard: this project's own top-level `calendar` package (`src/calendar/`) collides with the
*stdlib* `calendar` module the same way this project's `mcp` package collides with the SDK's.

Before the fix, calling `bootstrap_vendor_sdk()` (e.g. via any `import mcp.app`) in a fresh
process BEFORE anything had imported this project's own `calendar`/`tools` packages would leave
the *stdlib* `calendar` module permanently cached under the bare `calendar` name — breaking every
later `from calendar.trading_days import TradingDayCalendar` in `tools/market/` with
"'calendar' is not a package". This only ever passed accidentally in the existing test suite
because some other import (a conftest, a fixture module) always happened to import the real
`calendar` package first in the same process — this test isolates the ordering in a genuinely
fresh subprocess so the guard is exercised directly, not incidentally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")

_PROBE_SCRIPT = """
import sys
sys.path.insert(0, {src!r})

# Bootstrap the vendor SDK FIRST, before anything in this process has touched this project's
# own `calendar` or `tools` packages — the exact ordering that used to break.
from mcp._vendor import bootstrap_vendor_sdk
bootstrap_vendor_sdk()

# This import must resolve to the real project package, not the stdlib module the bootstrap's
# path-swapped probe import could otherwise have cached under the same name.
from calendar.trading_days import TradingDayCalendar
print("OK", TradingDayCalendar.__module__)
"""


def test_bootstrap_does_not_permanently_shadow_the_project_calendar_package() -> None:
    script = _PROBE_SCRIPT.format(src=_SRC)
    result = subprocess.run(  # noqa: S603 — fixed script, no shell, test-only
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK calendar.trading_days" in result.stdout
