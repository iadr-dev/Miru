"""Which Web Push endpoints the server will POST to.

A browser hands the server its own push-service URL at subscribe time, so that URL is user input
that the server later requests. Left unchecked it is a server-side request forgery primitive
(`http://169.254.169.254/...`, `http://localhost:6379`). Only the real push services are allowed:
HTTPS, and a hostname from this list, compared exactly (or as a dot-delimited suffix) after
lower-casing and stripping a trailing dot. No DNS resolution is involved, so there is nothing to
rebind.
"""

from __future__ import annotations

import json
from typing import cast
from urllib.parse import urlparse

# Exact hosts and the parent domains whose subdomains are the vendors' push services.
_EXACT_HOSTS = frozenset(
    {
        "fcm.googleapis.com",  # Chrome, Edge-on-Android, Brave, Opera
        "updates.push.services.mozilla.com",  # Firefox
        "web.push.apple.com",  # Safari
    }
)
_SUFFIX_DOMAINS = (
    ".push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",  # Edge (WNS)
)


def is_allowed_push_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return False
    return host in _EXACT_HOSTS or host.endswith(_SUFFIX_DOMAINS)


def subscription_endpoint_allowed(token: str) -> bool:
    """True if `token` is a JSON `PushSubscription` whose endpoint is an allowed push service."""
    try:
        parsed = json.loads(token)
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    endpoint: object = cast("dict[str, object]", parsed).get("endpoint")
    return isinstance(endpoint, str) and is_allowed_push_endpoint(endpoint)


__all__ = ["is_allowed_push_endpoint", "subscription_endpoint_allowed"]
