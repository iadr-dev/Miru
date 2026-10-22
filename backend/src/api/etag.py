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


def _request_if_none_match(scope: Scope) -> str:
    """The request's `If-None-Match` value (the last one if repeated), `""` when absent."""
    value = ""
    for name, raw in scope["headers"]:
        if name == b"if-none-match":
            value = raw.decode("latin-1")
    return value


def _is_taggable(start: Message) -> bool:
    """Only a plain 200 JSON response that sets no cookie and carries no tag of its own."""
    headers: list[tuple[bytes, bytes]] = list(start.get("headers", []))
    names = {name.lower() for name, _ in headers}
    content_type = next((v for n, v in headers if n.lower() == b"content-type"), b"")
    return (
        start["status"] == 200  # noqa: PLR2004
        and content_type.startswith(_JSON)
        and not names & _SKIP_HEADERS
        and b"etag" not in names
    )


def _tagged_headers(start: Message, etag: str) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = list(start.get("headers", []))
    headers.append((b"etag", etag.encode("latin-1")))
    names = {name.lower() for name, _ in headers}
    if b"cache-control" not in names:
        headers.append((b"cache-control", b"private, no-cache"))
    headers.append((b"vary", b"Authorization, Cookie"))
    return headers


async def _send_tagged(send: Send, start: Message, body: bytes, if_none_match: str) -> None:
    etag = etag_for(body)
    headers = _tagged_headers(start, etag)
    if if_none_match and _matches(if_none_match, etag):
        kept = [(n, v) for n, v in headers if n.lower() not in {b"content-length", b"content-type"}]
        await send({**start, "status": 304, "headers": kept})
        await send({"type": "http.response.body", "body": b""})
        return
    await send({**start, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _TaggingSend:
    """Wraps `send` for one response: buffers an eligible JSON body, then emits it tagged (or
    as a 304); anything else is forwarded untouched from the first message on."""

    def __init__(self, send: Send, if_none_match: str) -> None:
        self._send = send
        self._if_none_match = if_none_match
        self._start: Message | None = None
        self._chunks: list[bytes] = []
        self._passthrough = False

    async def __call__(self, message: Message) -> None:
        if self._passthrough:
            await self._send(message)
        elif message["type"] == "http.response.start":
            await self._on_start(message)
        else:
            await self._on_body(message)

    async def _on_start(self, message: Message) -> None:
        if _is_taggable(message):
            self._start = message
            return
        self._passthrough = True
        await self._send(message)

    async def _on_body(self, message: Message) -> None:
        # http.response.body for a buffered response
        self._chunks.append(message.get("body", b""))
        if message.get("more_body", False):
            return
        if self._start is None:  # a body with no start: not a well-formed response, leave it be
            await self._send(message)
            return
        await _send_tagged(self._send, self._start, b"".join(self._chunks), self._if_none_match)


class ConditionalGetMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self._app(scope, receive, send)
            return
        await self._app(scope, receive, _TaggingSend(send, _request_if_none_match(scope)))


__all__ = ["ConditionalGetMiddleware", "etag_for"]
