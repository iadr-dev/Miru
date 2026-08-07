"""A minimal ASGI SSE driver for the streaming routes.

`TestClient` and `httpx.ASGITransport` both buffer the whole response body, so neither can read an
*unbounded* SSE stream frame-by-frame or disconnect mid-stream — exactly what the live-quote and
`data.updated` feeds are. This harness invokes the real ASGI app directly (real routing, real
dependency resolution, the real route handler and its `StreamingResponse` generator), collecting
body chunks until a caller-supplied predicate is satisfied, then delivers `http.disconnect` so the
route's own cleanup (the demand-refcount decrement) runs — the genuine client-drop path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


async def collect_sse(
    app: object,
    path: str,
    *,
    until: Callable[[list[str]], bool],
    timeout_s: float = 5.0,
    on_started: Callable[[], Awaitable[None]] | None = None,
) -> list[str]:
    """Drive `GET {path}` against the ASGI `app` and return SSE lines once `until(lines)` holds
    (or the timeout elapses), then disconnect. `on_started` runs after the response has started
    but before reading — a hook to publish something the stream should then relay."""
    split = urlsplit(path)
    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": split.path,
        "raw_path": split.path.encode(),
        "query_string": split.query.encode(),
        "headers": [(b"host", b"test")],
        "server": ("test", 80),
        "client": ("test", 12345),
    }

    disconnect = asyncio.Event()
    started = asyncio.Event()
    body = bytearray()

    async def receive() -> dict[str, object]:
        if not started.is_set():
            started.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes | bytearray):
                body.extend(chunk)

    async def _run() -> None:
        await app(scope, receive, send)  # type: ignore[operator]

    task = asyncio.create_task(_run())
    try:
        if on_started is not None:
            await on_started()
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            lines = body.decode("utf-8", errors="ignore").splitlines()
            if until(lines):
                break
            await asyncio.sleep(0.02)
    finally:
        disconnect.set()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=2)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return body.decode("utf-8", errors="ignore").splitlines()


def quote_frames(lines: list[str]) -> list[dict[str, object]]:
    """Extract the JSON `data` payloads of `event: quote` frames from raw SSE lines."""
    out: list[dict[str, object]] = []
    kind: str | None = None
    for line in lines:
        if line.startswith("event: "):
            kind = line[len("event: ") :]
        elif line.startswith("data: ") and kind == "quote":
            out.append(json.loads(line[len("data: ") :]))
            kind = None
    return out


def frames_of_kind(lines: list[str], want: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    kind: str | None = None
    for line in lines:
        if line.startswith("event: "):
            kind = line[len("event: ") :]
        elif line.startswith("data: ") and kind == want:
            envelope = json.loads(line[len("data: ") :])
            payload = envelope.get("data", envelope) if isinstance(envelope, dict) else {}
            out.append(payload if isinstance(payload, dict) else {})
            kind = None
    return out
