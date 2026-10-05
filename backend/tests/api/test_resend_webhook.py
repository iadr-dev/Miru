"""Resend's webhook: Svix-signed bounce/complaint events put an address on the suppression list."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from scheduler.store import SqlAlchemySchedulerStore
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client

_KEY = b"resend-test-signing-key-bytes!!"
_SECRET = "whsec_" + base64.b64encode(_KEY).decode()
_URL = "/api/v1/webhooks/resend"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        resend_webhook_secret=_SECRET,
    )


@pytest.fixture
def client(settings: Settings):
    with build_test_client(settings, agent_answer="x") as test_client:
        yield test_client


def _signed(
    event: dict[str, object],
    *,
    key: bytes = _KEY,
    stamp: int | None = None,
    msg_id: str = "msg_1",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    ts = str(stamp if stamp is not None else int(time.time()))
    digest = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": ts,
        "svix-signature": "v1," + base64.b64encode(digest).decode(),
        "Content-Type": "application/json",
    }
    return body, headers


def _bounce(address: str, kind: str = "Permanent") -> dict[str, object]:
    return {
        "type": "email.bounced",
        "data": {
            "email_id": "e1",
            "to": [address],
            "bounce": {"type": kind, "subType": "General", "message": "550 no such user"},
        },
    }


def _suppressed(client: TestClient, address: str) -> bool:
    state = client.app.state.app_state
    assert client.portal is not None

    async def _check() -> bool:
        async with state.session_factory() as session:
            return await SqlAlchemySchedulerStore(session).is_email_suppressed(address=address)

    return client.portal.call(_check)


def test_a_hard_bounce_suppresses_the_address(client: TestClient) -> None:
    body, headers = _signed(_bounce("Gone@Example.test"))
    assert client.post(_URL, content=body, headers=headers).json() == {"suppressed": 1}
    assert _suppressed(client, "gone@example.test")  # stored lower-cased


def test_a_soft_bounce_delivery_and_other_events_change_nothing(client: TestClient) -> None:
    for event in (
        _bounce("soft@example.test", kind="Transient"),
        {"type": "email.delivered", "data": {"to": ["ok@example.test"]}},
        {"type": "email.opened", "data": {"to": ["ok@example.test"]}},
    ):
        body, headers = _signed(event)
        assert client.post(_URL, content=body, headers=headers).json() == {"suppressed": 0}
    assert not _suppressed(client, "soft@example.test")
    assert not _suppressed(client, "ok@example.test")


def test_a_spam_complaint_suppresses_the_address(client: TestClient) -> None:
    event = {"type": "email.complained", "data": {"to": ["mad@example.test"]}}
    body, headers = _signed(event)
    assert client.post(_URL, content=body, headers=headers).json() == {"suppressed": 1}
    assert _suppressed(client, "mad@example.test")


def test_resends_own_suppression_notice_is_recorded_too(client: TestClient) -> None:
    event = {"type": "email.suppressed", "data": {"to": ["known-bad@example.test"]}}
    body, headers = _signed(event)
    assert client.post(_URL, content=body, headers=headers).json() == {"suppressed": 1}
    assert _suppressed(client, "known-bad@example.test")


def test_a_bad_wrong_or_stale_signature_is_refused(client: TestClient) -> None:
    event = _bounce("x@example.test")
    body, _ = _signed(event)
    assert client.post(_URL, content=body).status_code == 403  # no signature at all
    wrong, wrong_headers = _signed(event, key=b"some-other-secret")
    assert client.post(_URL, content=wrong, headers=wrong_headers).status_code == 403
    old, old_headers = _signed(event, stamp=int(time.time()) - 3600)
    assert client.post(_URL, content=old, headers=old_headers).status_code == 403
    assert not _suppressed(client, "x@example.test")


def test_without_a_configured_secret_every_request_is_refused() -> None:
    bare = Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development", resend_webhook_secret=None)
    with build_test_client(bare, agent_answer="x") as unconfigured:
        body, headers = _signed(_bounce("x@example.test"))
        assert unconfigured.post(_URL, content=body, headers=headers).status_code == 403


def test_a_rotated_secret_with_two_signatures_still_verifies(client: TestClient) -> None:
    body, headers = _signed(_bounce("rot@example.test"))
    headers["svix-signature"] = "v1,AAAA " + headers["svix-signature"]  # old + current
    assert client.post(_URL, content=body, headers=headers).json() == {"suppressed": 1}
