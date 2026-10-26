"""Characterization of `resend_webhook` past the signature check: malformed bodies, non-dict
sections, recipient filtering, the recorded reason / event id, and the 403 error body."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from db.models.scheduler import EmailSuppressionRow
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client
from tests.api.test_resend_webhook import (
    _KEY,
    _SECRET,
    _URL,
    _bounce,
    _signed,
    _suppressed,
)


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


def _post(client: TestClient, event: dict[str, object]) -> dict[str, int]:
    body, headers = _signed(event)
    response = client.post(_URL, content=body, headers=headers)
    assert response.status_code == 200
    return response.json()


def _row(client: TestClient, address: str) -> tuple[str, str | None] | None:
    state = client.app.state.app_state
    assert client.portal is not None

    async def _get() -> tuple[str, str | None] | None:
        async with state.session_factory() as session:
            row = await session.get(EmailSuppressionRow, address)
            return None if row is None else (row.reason, row.event_id)

    return client.portal.call(_get)


def _post_raw(client: TestClient, body: bytes) -> dict[str, int]:
    ts = str(int(time.time()))
    digest = hmac.new(_KEY, f"m.{ts}.".encode() + body, hashlib.sha256).digest()
    headers = {
        "svix-id": "m",
        "svix-timestamp": ts,
        "svix-signature": "v1," + base64.b64encode(digest).decode(),
    }
    response = client.post(_URL, content=body, headers=headers)
    assert response.status_code == 200
    return response.json()


def test_a_signed_body_that_is_not_json_is_acknowledged_and_ignored(client: TestClient) -> None:
    assert _post_raw(client, b"not json {") == {"suppressed": 0}


def test_an_empty_signed_body_is_acknowledged_and_ignored(client: TestClient) -> None:
    assert _post_raw(client, b"") == {"suppressed": 0}


@pytest.mark.parametrize(
    "event",
    [
        {"type": "email.complained"},  # no data
        {"type": "email.complained", "data": "oops"},  # data not an object
        {"type": "email.complained", "data": ["a@example.test"]},
        {"data": {"to": ["a@example.test"]}},  # no type
        {"type": "email.bounced", "data": {"to": ["a@example.test"], "bounce": "Permanent"}},
        {"type": "email.bounced", "data": {"to": ["a@example.test"]}},  # no bounce object
        {"type": "email.complained", "data": {"to": "a@example.test"}},  # to not a list
        {"type": "email.complained", "data": {}},  # no recipients
    ],
)
def test_events_without_a_usable_shape_suppress_nothing(
    client: TestClient, event: dict[str, object]
) -> None:
    assert _post(client, event) == {"suppressed": 0}
    assert not _suppressed(client, "a@example.test")


def test_the_bounce_type_is_matched_case_insensitively(client: TestClient) -> None:
    assert _post(client, _bounce("lower@example.test", kind="permanent")) == {"suppressed": 1}
    assert _post(client, _bounce("upper@example.test", kind="PERMANENT")) == {"suppressed": 1}
    assert _post(client, _bounce("undet@example.test", kind="Undetermined")) == {"suppressed": 0}


def test_only_non_blank_string_recipients_are_counted_and_recorded(client: TestClient) -> None:
    event = {
        "type": "email.complained",
        "data": {
            "email_id": "evt-7",
            "to": ["one@example.test", "", "   ", 5, None, "Two@Example.test"],
        },
    }

    assert _post(client, event) == {"suppressed": 2}

    assert _row(client, "one@example.test") == ("complained", "evt-7")
    assert _row(client, "two@example.test") == ("complained", "evt-7")


def test_the_recorded_reason_is_bounced_for_a_bounce_and_a_resend_suppression(
    client: TestClient,
) -> None:
    _post(client, _bounce("b@example.test"))
    _post(client, {"type": "email.suppressed", "data": {"to": ["s@example.test"]}})

    assert _row(client, "b@example.test") == ("bounced", "e1")
    assert _row(client, "s@example.test") == ("bounced", None)  # no email_id -> no event id


def test_a_refused_signature_is_a_403_with_the_error_body(client: TestClient) -> None:
    response = client.post(_URL, content=b"{}")

    assert response.status_code == 403
    body = response.json()
    assert "Bad webhook signature." in str(body)
    assert "forbidden" in str(body)
