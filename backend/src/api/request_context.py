"""Request correlation and one structured access line per request.

Each request gets an id: the caller's `X-Request-ID` if it is a sane token, else a fresh one. The
id is bound into the logging context, so every log line written while serving the request carries
`request_id` (and `user_id` once the caller is resolved), and it is echoed back in the
`X-Request-ID` response header so a user's report can be matched to the server logs. After the
response, one `http_request` line records method, route template, status and duration. Pure ASGI,
so streaming (SSE) responses are not buffered."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
API_PREFIX = "/api/v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Probes fire every few seconds; logging each one buries the real traffic.
_QUIET_PATHS = frozenset({"/livez", "/readyz", "/startupz", "/healthz"})

logger = structlog.get_logger("http")


def _request_id(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = str(value.decode("latin-1"))
            if _SAFE_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _request_id(scope)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            path = scope["path"]
            if path not in _QUIET_PATHS:
                template = getattr(scope.get("route"), "path", None)
                if template and path.startswith(API_PREFIX) and not template.startswith(API_PREFIX):
                    template = f"{API_PREFIX}{template}"  # the matched route omits the mount prefix
                logger.info(
                    "http_request",
                    method=scope["method"],
                    path=template or "unmatched",
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()


__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]
