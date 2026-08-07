"""The events-calendar package: `calendar/` = rule engine + feed/scraper composition + state
model — not the stdlib `calendar` module.

**Naming collision, handled deliberately:** because this package is named `calendar` and sits
on `sys.path` (via the project's flat `src/` layout, `pyproject.toml` `pythonpath`/editable
install), it shadows the Python **standard-library** `calendar` module for any code importing
plain `import calendar` while `src/` precedes the stdlib on `sys.path`. Several third-party
libraries this project depends on (`httpx` → `http.cookiejar` → `from calendar import timegm`)
import the stdlib module by that exact name, so this package **re-exports the real stdlib
`calendar` module's public names** here, making this package a transparent superset: our own
submodules (`calendar.models`, `calendar.rules`, `calendar.trading_days`, `calendar.events`)
resolve as normal, and `from calendar import timegm`/`monthrange`/etc. still resolve to the
real stdlib implementations via re-export below — no functionality is lost either way.

`pyproject.toml` `[tool.ruff.lint.per-file-ignores]` already carries the `A005` ack for this
module name — this is the events calendar, not stdlib calendar.
"""

from __future__ import annotations

import importlib.util
import sys
import sysconfig
from pathlib import Path


def _load_stdlib_calendar() -> object:
    """Load the real stdlib `calendar.py` by absolute file path, bypassing normal
    `sys.path`-based import resolution (which would just re-import this very package)."""
    stdlib_dir = Path(sysconfig.get_paths()["stdlib"])
    stdlib_calendar_file = stdlib_dir / "calendar.py"
    spec = importlib.util.spec_from_file_location("_stdlib_calendar", stdlib_calendar_file)
    if spec is None or spec.loader is None:  # pragma: no cover — stdlib always ships this file
        raise ImportError("could not locate the standard-library calendar module")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_stdlib_calendar", module)
    spec.loader.exec_module(module)
    return module


_stdlib = _load_stdlib_calendar()

# Re-export every public stdlib name (classes, functions, constants like `JANUARY`, `timegm`,
# `monthrange`, `Calendar`, `TextCalendar`, ...) so `from calendar import X` keeps working for
# any dependency that imports the stdlib module by its conventional name.
for _name in dir(_stdlib):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_stdlib, _name)
    del _name  # always bound here — dir() on a real module is never empty

del _stdlib, _load_stdlib_calendar
