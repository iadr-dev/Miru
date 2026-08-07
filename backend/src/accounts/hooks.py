"""The `pre_tool` entitlement integration — how a real per-user tier reaches the sponsor gate.

`tools.base.dispatch_tool` already OWNS the sponsor-masking mechanism: when a tool's
`data_class == sponsor` and `ctx.tier` isn't entitled, it substitutes a `MaskedTeaser` instead
of running the tool. What was missing was the *resolution of the real tier* — the loop
previously ran with the stub `ToolContext(tier=Tier.FREE)`.

This module supplies exactly that and nothing more: `EntitlementResolver.tool_context_for`
resolves a `User` to the `Tier` the data-licensing axis uses (`entitlements.finmind_class_for`)
and returns a `ToolContext` carrying it. There is no second, redundant masking gate here — that
would duplicate `dispatch_tool` — and this module never imports the agent loop; the wiring layer
(above `accounts/`) builds the `ToolContext` once per turn and hands it to `AgentLoop.run_turn`,
which re-applies `dispatch_tool`'s gate per tool call (entitlement is resolved fresh on every
call rather than cached for the turn, so a mid-turn tier change takes effect immediately).

Naming mirrors Phase-7's `events/hooks.py` (`EventBusPostToolHook`, `MemoryRecallPreLLMHook`):
concrete handler built in a higher layer, plugged into a lower-layer seam by dependency
inversion, never an upward import.
"""

from __future__ import annotations

from accounts.entitlements import finmind_class_for
from accounts.models import User
from schemas.enums import TLSPolicy
from tools.base import ToolContext


class EntitlementResolver:
    """Resolves a `User` to the per-turn `ToolContext` the Step loop's sponsor gate consumes.

    The resolved `tier` is the *data-licensing* tier (`finmind_class_for`), which is exactly
    what `tools.base.is_entitled` compares `data_class == sponsor` against — a Premium/Pro user
    unmasks sponsor data, a Free/anonymous user gets the teaser."""

    def __init__(self, *, tls_policy: TLSPolicy = TLSPolicy.STRICT) -> None:
        self._tls_policy = tls_policy

    def tool_context_for(self, user: User | None) -> ToolContext:
        """Build the `ToolContext` for a turn. `None` = an anonymous caller → `Tier.FREE`
        (public data only)."""
        if user is None:
            return ToolContext(user_id=None, tls_policy=self._tls_policy)
        return ToolContext(
            user_id=user.id,
            tier=finmind_class_for(user),
            tls_policy=self._tls_policy,
        )


__all__ = ["EntitlementResolver"]
