"""Conditional GET for JSON reads: a weak `ETag` from the response body, and `304 Not Modified`
when the client already holds that exact body.

Market data changes once a day, but the web app re-fetches it on every page visit and the payloads
are large (rankings, heatmaps). The server still computes the answer, so this saves transfer and
client parse time, not server work. Correctness comes from the tag being a hash of the bytes sent:
anything that changes the answer (new data, a different tier's masking) changes the tag.

`Cache-Control: private, no-cache` makes the browser revalidate every time (so it can never show
a stale answer) and keeps a shared cache from storing a per-user body. Pure ASGI, and only
responses that are plain 200 JSON GETs are buffered; streams (SSE) pass through untouched.
"""

from __future__ import annotations

import hashlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_JSON = b"application/json"
# Never tag a response that sets a cookie (sign-in/refresh/logout): it is not a cacheable read.
_SKIP_HEADERS = frozenset({b"set-cookie"})


def etag_for(body: bytes) -> str:
    digest = hashlib.sha1(body, usedforsecurity=False).hexdigest()  # a tag, not a secret
    return f'W/"{digest}"'


def _matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 weak comparison: `*` or any listed tag equal to ours, ignoring the weak prefix."""
    wanted = etag.removeprefix("W/")
    listed = [part.strip() for part in if_none_match.split(",")]
    return any(tag == "*" or tag.removeprefix("W/") == wanted for tag in listed)


class ConditionalGetMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self._app(scope, receive, send)
            return

        if_none_match = ""
        for name, value in scope["headers"]:
            if name == b"if-none-match":
                if_none_match = value.decode("latin-1")

        start: Message | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def intercept(message: Message) -> None:
            nonlocal start, passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                names = {name.lower() for name, _ in headers}
                content_type = next((v for n, v in headers if n.lower() == b"content-type"), b"")
                eligible = (
                    message["status"] == 200  # noqa: PLR2004
                    and content_type.startswith(_JSON)
                    and not names & _SKIP_HEADERS
                    and b"etag" not in names
                )
                if not eligible:
                    passthrough = True
                    await send(message)
                    return
                start = message
                return
            # http.response.body for a buffered response
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            if start is None:  # a body with no start: not a well-formed response, leave it be
                await send(message)
                return
            body = b"".join(chunks)
            etag = etag_for(body)
            headers = list(start.get("headers", []))
            headers.append((b"etag", etag.encode("latin-1")))
            names = {name.lower() for name, _ in headers}
            if b"cache-control" not in names:
                headers.append((b"cache-control", b"private, no-cache"))
            headers.append((b"vary", b"Authorization, Cookie"))
            if if_none_match and _matches(if_none_match, etag):
                kept = [
                    (n, v)
                    for n, v in headers
                    if n.lower() not in {b"content-length", b"content-type"}
                ]
                await send({**start, "status": 304, "headers": kept})
                await send({"type": "http.response.body", "body": b""})
                return
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self._app(scope, receive, intercept)


__all__ = ["ConditionalGetMiddleware", "etag_for"]
