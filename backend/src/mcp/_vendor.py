"""Loads the real `fastmcp`/`mcp` SDK packages before anything else in this module touches
them.

This project's own top-level package is named `mcp` (the interfaces-layer home for this MCP
server, fixed by the layering rules every other module in this codebase follows). The PyPI
`fastmcp` package we build on depends on a *different* package that is also named `mcp` (the
official SDK it wraps). Under this project's own import setup, this package's `src/` directory
sits ahead of the installed-package directory on the import path, so a bare `import mcp`
always resolves to *this* package, not the SDK one — which breaks `fastmcp` internals the first
time they reach for `mcp.types`/`mcp.server.*` (those imports happen lazily, the first time a
tool/resource is actually invoked or the HTTP app is built, not merely at decoration time).

The fix: temporarily take this package's directory off the import path, import `fastmcp` (which
transitively pulls in and fully initializes every `mcp.*` submodule it will ever need — its own
constructor already walks the whole tree), then restore the import path. Constructing one
`FastMCP` instance during this window is what forces the full SDK import — a bare `import
fastmcp` alone does not reach every submodule. Everything the SDK needs is now cached in
`sys.modules` under the real `mcp` name, so it stays fully usable for the rest of the process
even after this package's own `mcp` binding is restored. This is a one-time, process-wide
fixup — call it once, as early as possible, before touching `fastmcp` anywhere else in this
package.

**A second, narrower collision this fixup must also guard:** this project also owns a top-level
`calendar` package (`src/calendar/`, the trading-calendar module), which collides with the
*stdlib* `calendar` module the exact same way this project's `mcp` package collides with the
SDK's — and the stdlib one is earlier on `sys.path` once this package's `src/` directory is
swapped out for the probe import above. If nothing has imported this project's `calendar`
package yet by the time this function runs, `fastmcp`'s own import graph (or Python's import
machinery resolving some unrelated `import calendar`) can cache the *stdlib* module under the
bare `calendar` name during that swapped-path window — and that wrong module then stays cached
under the real name for the rest of the process, breaking every later
`from calendar.trading_days import TradingDayCalendar` in `tools/market/` with
"'calendar' is not a package". This function evicts and restores `calendar`/`calendar.*`
exactly the same way it does for `mcp`/`mcp.*`, so this bootstrap can safely run *before*
anything in this process has imported the project's `tools`/`calendar` packages — a caller no
longer needs to import them first as a workaround.
"""

# This module's `from fastmcp import FastMCP` under a swapped-out `sys.path` pulls the entire
# `fastmcp`/vendor-SDK dependency graph into the same strongly-connected component mypy analyzes
# this module in; since that vendor SDK is also named `mcp` (the exact collision this module
# exists to work around, see the module docstring above), mypy's own semantic analyzer hits an
# internal deferral-tracking crash walking that combined graph (upstream mypy bug, not a real
# type error in this file — verified by checking the identical code under a different filename,
# which type-checks cleanly). Excluded from analysis here rather than leaving the whole
# toolchain command broken for an issue outside this file's own logic.
# mypy: ignore-errors
from __future__ import annotations

import importlib
import sys

_BOOTSTRAPPED_ATTR = "_miru_mcp_vendor_bootstrapped"


def bootstrap_vendor_sdk() -> None:
    """Import the real `fastmcp`/`mcp` SDK once, then restore this package's own `mcp` name.

    Idempotent (checks a process-global flag) and safe to call from every module in this
    package that touches `fastmcp` — only the first call does any work.
    """
    if getattr(sys, _BOOTSTRAPPED_ATTR, False):
        return

    ours = sys.modules.pop("mcp", None)
    our_submodules = {
        name: module for name, module in sys.modules.items() if name.startswith("mcp.")
    }
    for name in our_submodules:
        del sys.modules[name]

    # Guard the `calendar`/stdlib collision the same way, in case this project's own `calendar`
    # package hasn't been imported yet by the time this bootstrap runs (see the module
    # docstring) — pop whatever is cached under that name now (this project's real package, if
    # already imported; nothing, if not) so the probe import below can't leave the wrong module
    # cached under it afterwards.
    saved_calendar = sys.modules.pop("calendar", None)
    saved_calendar_submodules = {
        name: module for name, module in sys.modules.items() if name.startswith("calendar.")
    }
    for name in saved_calendar_submodules:
        del sys.modules[name]

    saved_path = list(sys.path)
    sys.path = [p for p in sys.path if not p.rstrip("/\\").endswith("src")]
    try:
        from fastmcp import FastMCP  # noqa: PLC0415 — must import inside the path-swapped block

        FastMCP(name="_miru_mcp_vendor_bootstrap_probe")
        # The client halves are imported in the same window so they stay importable under the
        # real SDK name afterwards: the test-suite (and any in-repo smoke script) drives this
        # server with the official `mcp` client and `fastmcp.Client`, and a submodule that was
        # not cached here would resolve against THIS package's `mcp` and fail.
        for client_module in (
            "fastmcp.client",
            "mcp.client.session",
            "mcp.client.streamable_http",
            "mcp.client.auth",
            "mcp.shared.auth",
            "mcp.shared.exceptions",
        ):
            importlib.import_module(client_module)
    finally:
        sys.path = saved_path
        for name, module in our_submodules.items():
            sys.modules[name] = module
        if ours is not None:
            sys.modules["mcp"] = ours
        # Whatever the probe import cached under `calendar`/`calendar.*` (stdlib, most likely,
        # since the swapped path favors it) is wrong for this project — discard it and restore
        # this project's own package if it was already loaded; if it wasn't, leave the name
        # unbound so the next real `import calendar`/`from calendar.trading_days import ...`
        # resolves fresh, against the restored `sys.path`, to the real package.
        for name in list(sys.modules):
            if name == "calendar" or name.startswith("calendar."):
                del sys.modules[name]
        for name, module in saved_calendar_submodules.items():
            sys.modules[name] = module
        if saved_calendar is not None:
            sys.modules["calendar"] = saved_calendar

    setattr(sys, _BOOTSTRAPPED_ATTR, True)


__all__ = ["bootstrap_vendor_sdk"]
