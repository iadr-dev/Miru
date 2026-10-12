"""The outbound seams the external channels send through.

A channel shapes the request (`OutboundRequest`, unit-testable with no socket); a transport puts it
on the wire. Keeping the two apart means every channel is tested against a fake transport, and the
live `httpx` one is the only piece that opens a socket.

Secrets discipline: a request's headers/body carry credentials and recipient addresses. Nothing in
this module logs them — only the channel-agnostic outcome (status class) is ever logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

import anyio.to_thread
import httpx
import requests
import structlog
from pywebpush import WebPushException, webpush  # pyright: ignore[reportUnknownVariableType]

from netclient import lease_client

logger = structlog.get_logger(__name__)

# httpx/httpcore log every request URL at INFO, and a Telegram bot token is part of the URL path.
# Pin them above INFO so a credential never reaches the logs through a third-party logger.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_TIMEOUT_SECONDS = 10.0
_HTTP_OK_MAX = 299
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERROR = 500


@dataclass(frozen=True)
class TransportResult:
    """What the wire said. `retryable` is the transport's judgement (network failure, 429, 5xx);
    a 4xx (bad token, blocked bot, unknown chat) will not get better by trying again."""

    ok: bool
    status: int | None = None
    retryable: bool = False
    message_id: str | None = None
    detail: str = ""
    # The push service says this subscription is gone for good (404/410): prune it.
    gone: bool = False


@runtime_checkable
class OutboundTransport(Protocol):
    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult: ...


class HttpxTransport:
    """The live transport: one JSON POST per call, bounded by a short timeout so a hung provider
    cannot stall the scheduler pass."""

    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult:
        try:
            async with lease_client(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json=body)
        except (httpx.HTTPError, OSError):
            logger.warning("outbound_transport_error")
            return TransportResult(ok=False, retryable=True, detail="network error")
        return _classify(response.status_code, response.headers.get("x-line-request-id"))


def _classify(status: int, request_id: str | None) -> TransportResult:
    if status <= _HTTP_OK_MAX:
        return TransportResult(ok=True, status=status, message_id=request_id)
    retryable = status == _HTTP_TOO_MANY or status >= _HTTP_SERVER_ERROR
    return TransportResult(ok=False, status=status, retryable=retryable, detail=f"http {status}")


_HTTP_NOT_FOUND = 404
_HTTP_GONE = 410
_PUSH_TTL_SECONDS = 3600


class WebPushSender(Protocol):
    async def send(self, *, subscription: dict[str, object], payload: str) -> TransportResult: ...


class PywebpushSender:
    """Web Push (RFC 8030) with VAPID. The payload is encrypted to the browser's own key and relayed
    by its push service (Google/Mozilla/Apple), so only the subscribed browser can read it. The
    library blocks on the HTTP call, so it runs in a worker thread."""

    def __init__(self, *, private_key: str, subject: str) -> None:
        self._private_key = private_key
        self._subject = subject

    async def send(self, *, subscription: dict[str, object], payload: str) -> TransportResult:
        return await anyio.to_thread.run_sync(self._send_blocking, subscription, payload)

    def _send_blocking(self, subscription: dict[str, object], payload: str) -> TransportResult:
        # A push service never needs to redirect; refusing to follow one keeps an allow-listed host
        # from bouncing the request to an internal address.
        session = requests.Session()
        session.max_redirects = 0
        try:
            webpush(
                requests_session=session,
                subscription_info=subscription,
                data=payload,
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                ttl=_PUSH_TTL_SECONDS,
            )
        except WebPushException as exc:
            response = cast("httpx.Response | None", exc.response)
            status: int | None = response.status_code if response is not None else None
            gone = status in (_HTTP_NOT_FOUND, _HTTP_GONE)
            retryable = status is None or status == _HTTP_TOO_MANY or status >= _HTTP_SERVER_ERROR
            logger.warning("web_push_rejected", status=status)
            return TransportResult(
                ok=False, status=status, retryable=retryable and not gone, gone=gone, detail="push"
            )
        except Exception:
            logger.warning("web_push_failed")
            return TransportResult(ok=False, retryable=False, detail="push error")
        return TransportResult(ok=True)
