"""Paddle webhook signature verification + envelope parsing.

The security-critical half of the billing surface. Paddle signs each webhook over the *exact,
unmodified raw request body*; re-serializing parsed JSON would change the bytes and break
verification, so the caller must hand us the raw bytes read before any JSON parsing.

Signature header format: `ts=<unix_ts>;h1=<hex_hmac_sha256>`. The signed payload is
`f"{ts}:{raw_body}"`, HMAC-SHA256 with the webhook secret as the key. We compare hex digests
with a constant-time comparison (never `==`, which leaks timing) and reject any timestamp older
than a short tolerance to defend against replay of a captured request.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# Paddle's own recommended replay tolerance. A signature whose timestamp is older than this is
# rejected even if the HMAC matches — a captured-and-replayed request must not re-apply.
_MAX_SIGNATURE_AGE_SECONDS = 5


class SignatureVerificationError(RuntimeError):
    """The webhook signature was missing, malformed, stale, or did not match — the request is not
    trusted and must not be processed."""


class PaddleEvent(BaseModel):
    """The Paddle webhook envelope. `data` is the entity object (e.g. a subscription); it stays a
    raw mapping here because different event types carry different entities — the service layer
    interprets it per `event_type`."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_id: str
    event_type: str
    occurred_at: datetime
    notification_id: str | None = None
    data: dict[str, Any]


def _parse_signature_header(header: str) -> tuple[int, str]:
    """Split `ts=...;h1=...` into `(ts, h1)`. Tolerant of ordering and extra parts, strict about
    the two we require being present and well-formed."""
    parts: dict[str, str] = {}
    for segment in header.split(";"):
        key, sep, value = segment.partition("=")
        if sep:
            parts[key.strip()] = value.strip()
    ts_raw = parts.get("ts")
    h1 = parts.get("h1")
    if not ts_raw or not h1:
        raise SignatureVerificationError("signature header missing ts or h1")
    try:
        ts = int(ts_raw)
    except ValueError as exc:
        raise SignatureVerificationError("signature timestamp is not an integer") from exc
    return ts, h1


def verify_signature(
    raw_body: bytes,
    header: str | None,
    secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Verify a `Paddle-Signature` header against the raw request bytes.

    Returns `True` on a valid, fresh signature; raises `SignatureVerificationError` otherwise so
    the router can answer a specific 4xx. `now` is injectable for deterministic testing of the
    freshness window.
    """
    if not header:
        raise SignatureVerificationError("missing signature header")
    if not secret:
        raise SignatureVerificationError("webhook secret not configured")

    ts, provided_h1 = _parse_signature_header(header)

    current = time.time() if now is None else now
    if current - ts > _MAX_SIGNATURE_AGE_SECONDS:
        raise SignatureVerificationError("signature timestamp is stale")

    signed_payload = f"{ts}:".encode() + raw_body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, provided_h1):
        raise SignatureVerificationError("signature mismatch")
    return True


def parse_event(raw_body: bytes) -> PaddleEvent:
    """Parse the verified raw body into the typed envelope. Call only after `verify_signature`."""
    return PaddleEvent.model_validate_json(raw_body)


__all__ = [
    "PaddleEvent",
    "SignatureVerificationError",
    "parse_event",
    "verify_signature",
]
