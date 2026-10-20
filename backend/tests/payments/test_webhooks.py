"""Paddle webhook signature verification + envelope parsing.

The HMAC path is security-critical, so these are governance-marked: a valid signature over the
exact raw bytes accepts, any tampering with the body rejects, a stale timestamp rejects, and the
comparison is constant-time (we assert on behavior, and the impl uses `hmac.compare_digest`).
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from payments.webhooks import (
    SignatureVerificationError,
    parse_event,
    verify_signature,
)

_SECRET = "whsec-test"


def _sign(raw_body: bytes, *, ts: int, secret: str = _SECRET) -> str:
    signed = f"{ts}:".encode() + raw_body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


pytestmark = pytest.mark.governance


def test_valid_signature_accepts() -> None:
    body = b'{"event_id":"evt_1","event_type":"subscription.created"}'
    ts = int(time.time())
    header = _sign(body, ts=ts)
    assert verify_signature(body, header, _SECRET, now=ts) is True


def test_tampered_body_rejects() -> None:
    body = b'{"event_id":"evt_1","event_type":"subscription.created"}'
    ts = int(time.time())
    header = _sign(body, ts=ts)
    tampered = body.replace(b"created", b"canceled")
    with pytest.raises(SignatureVerificationError):
        verify_signature(tampered, header, _SECRET, now=ts)


def test_stale_timestamp_rejects() -> None:
    body = b'{"event_id":"evt_1"}'
    ts = 1_000_000
    header = _sign(body, ts=ts)
    # A signature that is otherwise valid but whose ts is well past the freshness window.
    with pytest.raises(SignatureVerificationError):
        verify_signature(body, header, _SECRET, now=ts + 60)


def test_wrong_secret_rejects() -> None:
    body = b'{"event_id":"evt_1"}'
    ts = int(time.time())
    header = _sign(body, ts=ts, secret="different-secret")
    with pytest.raises(SignatureVerificationError):
        verify_signature(body, header, _SECRET, now=ts)


def test_missing_header_rejects() -> None:
    with pytest.raises(SignatureVerificationError):
        verify_signature(b"{}", None, _SECRET)


def test_malformed_header_rejects() -> None:
    now = int(time.time())
    with pytest.raises(SignatureVerificationError):
        verify_signature(b"{}", "not-a-valid-header", _SECRET, now=now)


def test_missing_secret_rejects() -> None:
    body = b"{}"
    ts = int(time.time())
    signature = _sign(body, ts=ts)
    with pytest.raises(SignatureVerificationError):
        verify_signature(body, signature, "", now=ts)


def test_signature_is_bound_to_exact_bytes_not_reserialized_json() -> None:
    # Two JSON encodings with the same logical content but different bytes (whitespace) must not
    # both verify — the signature is over the raw bytes, so re-serializing would break it.
    body = b'{"a": 1,"b":2}'
    ts = int(time.time())
    header = _sign(body, ts=ts)
    reserialized = b'{"a":1,"b":2}'  # semantically identical, different bytes
    assert verify_signature(body, header, _SECRET, now=ts) is True
    with pytest.raises(SignatureVerificationError):
        verify_signature(reserialized, header, _SECRET, now=ts)


def test_parse_event_reads_envelope() -> None:
    body = (
        b'{"event_id":"evt_9","event_type":"subscription.updated",'
        b'"occurred_at":"2024-05-01T12:00:00Z","notification_id":"ntf_1",'
        b'"data":{"id":"sub_1","status":"active"}}'
    )
    event = parse_event(body)
    assert event.event_id == "evt_9"
    assert event.event_type == "subscription.updated"
    assert event.data["id"] == "sub_1"
