"""`mcp.principal` — the entitlement-resolution seam. No credential (or an unrecognized one)
must resolve to anonymous/`Tier.FREE`; a resolver that recognizes a key must resolve to that
key's `(user_id, tier)`."""

from __future__ import annotations

from mcp.principal import resolve_principal
from schemas.enums import Tier
from tools.base import ToolContext


class _StubResolver:
    def __init__(self, known: dict[str, tuple[str, Tier]]) -> None:
        self._known = known

    async def resolve(self, api_key: str) -> tuple[str, Tier] | None:
        return self._known.get(api_key)


async def test_no_credential_resolves_to_anonymous_free_tier() -> None:
    ctx = await resolve_principal({})

    assert ctx == ToolContext()
    assert ctx.tier is Tier.FREE
    assert ctx.user_id is None


async def test_unknown_api_key_falls_back_to_anonymous_default() -> None:
    ctx = await resolve_principal({"x-api-key": "unknown-key"}, resolver=_StubResolver({}))

    assert ctx.tier is Tier.FREE
    assert ctx.user_id is None


async def test_recognized_api_key_resolves_to_its_user_and_tier() -> None:
    resolver = _StubResolver({"good-key": ("user-42", Tier.PRO)})
    ctx = await resolve_principal({"x-api-key": "good-key"}, resolver=resolver)

    assert ctx.user_id == "user-42"
    assert ctx.tier is Tier.PRO


async def test_default_resolver_never_elevates_any_key() -> None:
    """With no resolver injected at all, every key — however plausible-looking — resolves to
    the safe anonymous default, never a hard error and never an elevated tier."""
    ctx = await resolve_principal({"x-api-key": "some-plausible-key"})

    assert ctx.tier is Tier.FREE
    assert ctx.user_id is None
