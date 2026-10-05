"""Signed unsubscribe links for email notifications (RFC 8058 one-click).

The link carries no stored state: `<user id>.<signature>`, where the signature is an HMAC of the
user id and their *current* email address under the server secret. So it cannot be forged, it cannot
be used to unsubscribe anyone else, and it stops working if the account's email changes.
"""

from __future__ import annotations

import hashlib
import hmac

_PURPOSE = b"email-unsubscribe"
_SIGNATURE_CHARS = 32


def _signature(secret: str, user_id: str, email: str) -> str:
    message = b"\x00".join([_PURPOSE, user_id.encode(), email.strip().lower().encode()])
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:_SIGNATURE_CHARS]


def make_token(secret: str, user_id: str, email: str) -> str:
    return f"{user_id}.{_signature(secret, user_id, email)}"


def split_token(token: str) -> tuple[str, str] | None:
    """`(user id, signature)` from a token, or None if it is not shaped like one."""
    user_id, dot, signature = token.rpartition(".")
    return (user_id, signature) if dot and user_id and signature else None


def verify_token(secret: str, token: str, user_id: str, email: str) -> bool:
    parts = split_token(token)
    if parts is None or parts[0] != user_id:
        return False
    return hmac.compare_digest(parts[1].encode(), _signature(secret, user_id, email).encode())


def unsubscribe_url(base_url: str, secret: str, user_id: str, email: str) -> str | None:
    """The one-click URL, or None when `base_url` is not HTTPS — mail providers ignore (or
    penalise) a List-Unsubscribe that is not."""
    if not base_url.startswith("https://"):
        return None
    token = make_token(secret, user_id, email)
    return f"{base_url.rstrip('/')}/api/v1/email/unsubscribe?token={token}"


__all__ = ["make_token", "split_token", "unsubscribe_url", "verify_token"]
