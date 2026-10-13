"""An open SSE stream must not hold a database session.

`/updates/stream` and `/quote/stream` stay open for as long as a browser tab does. The
request-scoped session commits and closes only when the response ends, so resolving the caller on
it left one pooled connection and one open transaction ("idle in transaction") per open tab: about
twenty tabs exhausted the pool and the health probes, and the open transactions blocked every
`ALTER TABLE` at startup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in
from tests.api.sse_harness import collect_sse


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")


class _IdleQuoteFeed:
    """A quote feed whose stream never produces anything, like a quiet market."""

    def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            await asyncio.sleep(3600)
            yield ""

        return gen()


class _Tracking:
    """Wraps the app's session factory and remembers every session it hands out."""

    def __init__(self, real: object) -> None:
        self._real = real
        self.sessions: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        session = self._real(*args, **kwargs)  # type: ignore[operator]
        self.sessions.append(session)
        return session

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@pytest.mark.parametrize("tier", [Tier.FREE, Tier.PRO])
@pytest.mark.parametrize("path", ["/api/v1/updates/stream", "/api/v1/quote/stream?symbols=2330"])
def test_an_open_stream_holds_no_open_database_transaction(
    settings: Settings, tier: Tier, path: str
) -> None:
    with build_test_client(settings, agent_answer="x", quote_feed=_IdleQuoteFeed()) as client:
        headers, _ = sign_in(client, settings, tier=tier)
        state = client.app.state.app_state  # type: ignore[attr-defined]
        tracking = _Tracking(state.session_factory)
        object.__setattr__(state, "session_factory", tracking)
        observed: dict[str, object] = {}

        async def inspect_while_streaming() -> None:
            await asyncio.sleep(0.6)  # long enough for the route to resolve the caller and start
            observed["sessions"] = len(tracking.sessions)
            observed["open"] = [s for s in tracking.sessions if s.in_transaction()]  # type: ignore[attr-defined]

        assert client.portal is not None
        client.portal.call(
            lambda: collect_sse(
                client.app,
                path,
                until=lambda _lines: "open" in observed,
                timeout_s=5,
                on_started=inspect_while_streaming,
                headers=headers,
            )
        )

        assert observed["sessions"] >= 1, "the caller was resolved through a session"
        assert observed["open"] == [], (
            "no session may stay in a transaction while the stream is open"
        )
