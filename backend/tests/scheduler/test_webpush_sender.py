"""`PywebpushSender` against a local stand-in for a browser's push service: the request really is a
VAPID-signed, encrypted Web Push, and the push service's answers map to the right outcome."""

from __future__ import annotations

import base64
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid

from scheduler.delivery.transport import PywebpushSender


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _vapid_private_key() -> str:
    vapid = Vapid()
    vapid.generate_keys()
    der = vapid.private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return _b64(der)


def _browser_keys() -> dict[str, str]:
    """What a browser hands over on subscribe: its P-256 public key and a 16-byte auth secret."""
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    point = public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {"p256dh": _b64(point), "auth": _b64(os.urandom(16))}


class _PushService:
    """Records what it was sent and answers with a configurable status."""

    def __init__(self) -> None:
        self.status = 201
        self.requests: list[tuple[dict[str, str], int]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                outer.requests.append(({k.lower(): v for k, v in self.headers.items()}, length))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/push/abc"

    def __enter__(self) -> _PushService:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()


@pytest.fixture
def service() -> Iterator[_PushService]:
    with _PushService() as svc:
        yield svc


def _sender() -> PywebpushSender:
    return PywebpushSender(private_key=_vapid_private_key(), subject="mailto:test@example.test")


async def test_a_push_is_vapid_signed_and_encrypted(service: _PushService) -> None:
    result = await _sender().send(
        subscription={"endpoint": service.endpoint, "keys": _browser_keys()},
        payload='{"title":"提醒","body":"x"}',
    )
    assert result.ok is True
    headers, length = service.requests[0]
    assert headers["authorization"].lower().startswith("vapid")
    assert headers["content-encoding"] == "aes128gcm"  # the payload is encrypted, not plaintext
    assert length > len('{"title":"提醒","body":"x"}')


@pytest.mark.parametrize(
    ("status", "gone", "retryable"),
    [(410, True, False), (404, True, False), (503, False, True), (403, False, False)],
)
async def test_the_push_services_answer_decides_prune_retry_or_give_up(
    service: _PushService, status: int, gone: bool, retryable: bool
) -> None:
    service.status = status
    result = await _sender().send(
        subscription={"endpoint": service.endpoint, "keys": _browser_keys()}, payload="{}"
    )
    assert result.ok is False
    assert result.gone is gone
    assert result.retryable is retryable


async def test_a_malformed_subscription_is_a_failure_not_a_crash() -> None:
    result = await _sender().send(
        subscription={"endpoint": "http://127.0.0.1:1/x", "keys": {"p256dh": "!", "auth": "!"}},
        payload="{}",
    )
    assert result.ok is False


async def test_the_sender_does_not_follow_a_redirect(service: _PushService) -> None:
    service.status = 302  # a push service bouncing the request elsewhere
    result = await _sender().send(
        subscription={"endpoint": service.endpoint, "keys": _browser_keys()}, payload="{}"
    )
    assert result.ok is False
    assert len(service.requests) == 1  # it stopped at the first response
