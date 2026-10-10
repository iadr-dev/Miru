"""Flag responses served to a caller whose access token had expired.

Public data routes resolve the caller with `optional_user`, which must not fail a request just
because a credential is bad: a logged-out visitor with a stale cookie still gets the public
(free-tier) answer. But a signed-in Pro user whose ~15-minute access token lapsed would then be
served free-tier teasers as a normal HTTP 200, and the client, seeing no 401, would never refresh
the session. `optional_user` therefore records the expiry on the request, and this middleware
adds an `X-Auth-Expired: 1` header to that response so the client can refresh and retry.

Pure ASGI (not `BaseHTTPMiddleware`) so it never buffers or reorders a streaming/SSE response.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

AUTH_EXPIRED_HEADER = "X-Auth-Expired"
AUTH_EXPIRED_STATE_KEY = "auth_expired"


class AuthExpiredHeaderMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_flag(message: Message) -> None:
            if message["type"] == "http.response.start":
                state = scope.get("state")
                flags: Mapping[str, object] = (
                    cast("Mapping[str, object]", state) if isinstance(state, Mapping) else {}
                )
                if flags.get(AUTH_EXPIRED_STATE_KEY):
                    headers = list(message.get("headers", []))
                    headers.append((AUTH_EXPIRED_HEADER.lower().encode("latin-1"), b"1"))
                    message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_with_flag)
