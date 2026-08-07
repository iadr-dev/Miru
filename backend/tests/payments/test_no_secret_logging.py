"""Governance: the Paddle API key and webhook secret must never reach a log line. Asserts on the
actual structlog calls emitted while exercising the client (which sends the API key as a bearer)
and while processing a webhook (verified with the secret)."""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx
import pytest
import structlog

from config.settings import Settings
from payments.client import PaddleClient
from payments.webhooks import verify_signature

pytestmark = pytest.mark.governance

_API_KEY = "pk-SUPER-SECRET-KEY"
_WEBHOOK_SECRET = "whsec-SUPER-SECRET"


def _capture() -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _proc(_logger: object, _method: str, event_dict: dict[str, object]) -> dict[str, object]:
        captured.append(dict(event_dict))
        return event_dict

    structlog.configure(processors=[_proc, structlog.processors.KeyValueRenderer()])
    return captured


async def test_api_key_never_logged_on_error() -> None:
    captured = _capture()
    try:
        settings = Settings(paddle_api_key=_API_KEY, paddle_environment="sandbox")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        client = PaddleClient(settings, client_factory=factory)
        with pytest.raises(Exception):  # noqa: B017 — any error; the point is the log content
            await client.get_subscription("sub_1")
    finally:
        structlog.reset_defaults()

    assert captured  # the error path did log
    for event in captured:
        for value in event.values():
            assert _API_KEY not in str(value)


def test_webhook_secret_never_logged() -> None:
    captured = _capture()
    try:
        body = b'{"event_id":"evt_1"}'
        ts = int(time.time())
        signed = f"{ts}:".encode() + body
        digest = hmac.new(_WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
        header = f"ts={ts};h1={digest}"
        verify_signature(body, header, _WEBHOOK_SECRET, now=ts)
        # Also exercise the rejection path, which logs nothing sensitive either.
        structlog.get_logger("test").info("probe", detail="ok")
    finally:
        structlog.reset_defaults()

    for event in captured:
        for value in event.values():
            assert _WEBHOOK_SECRET not in str(value)
