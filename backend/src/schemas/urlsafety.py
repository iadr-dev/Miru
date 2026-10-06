"""Which URLs may ever be fetched or cited: public http/https only, no credentials.

Shared by the research ledger (what may be cited) and the hardened page fetch in `sources/`
(what may be requested, on every redirect hop). Pure string/IP logic - no network.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "spm", "igshid")
_INTERNAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def is_public_host(host: str) -> bool:
    """A name or address that can only be on the public internet."""
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    labels = host.split(".")
    if _is_numeric_label(labels[-1]):
        return (
            False  # "2130706433", "127.1", "0x7f.0.0.1": legacy IPv4 spellings; no TLD is numeric
        )
    return len(labels) > 1  # a bare hostname is an intranet name


def _is_numeric_label(label: str) -> bool:
    if label.startswith("0x"):
        return True
    return label.isdigit()


def canonical_url(url: str) -> str | None:
    """A normalised, fetch-safe URL, or None if it must never be fetched or cited."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not is_public_host(host):
        return None
    if parts.username or parts.password:
        return None
    netloc = host if port in (None, _DEFAULT_PORTS[parts.scheme]) else f"{host}:{port}"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith(_TRACKING_PARAMS)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path or "/", query, ""))


__all__ = ["canonical_url", "is_public_host"]
