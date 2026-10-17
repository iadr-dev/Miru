"""The pure rules of the authorization server: PKCE, redirect URIs, scopes, resource matching.

No I/O and no database - everything here is a function of its arguments, so each rule is tested
directly. The security-relevant ones:

- PKCE is S256 only (a `plain` challenge protects nothing once the request is observed).
- A redirect URI must match a registered one exactly; the single exception is a loopback `http`
  URI, whose port is ignored (RFC 8252 section 7.3: a native app takes a free port at run time).
- A registered redirect URI is https, loopback http, or a custom scheme the operator allow-listed;
  never `javascript:`/`data:`/`file:`, never a fragment, never userinfo, never a wildcard.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
from collections.abc import Collection, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from accounts.mcp_tokens import MCP_SCOPE_READ, SUPPORTED_SCOPES

MAX_REDIRECT_URI_LENGTH = 2048
MAX_REDIRECT_URIS = 10

# S256 of a 32..96 byte verifier is 43 base64url characters (RFC 7636 section 4.2).
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# RFC 7636 section 4.1: 43..128 characters of unreserved URI characters.
_CODE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# The only spellings of a loopback http authority that are accepted: lower case, no userinfo,
# no trailing dot, no percent-encoding, an optional numeric port.
_LOOPBACK_NETLOC = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?")
_FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "blob", "about", "ftp"})


class RedirectUriError(ValueError):
    """The redirect URI is not an acceptable registration."""


def s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_valid_code_challenge(challenge: str) -> bool:
    return _CODE_CHALLENGE.fullmatch(challenge) is not None


def verify_pkce(*, verifier: str, challenge: str) -> bool:
    if _CODE_VERIFIER.fullmatch(verifier) is None:
        return False
    return hmac.compare_digest(s256_challenge(verifier), challenge)


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    return host.lower() in _LOOPBACK_HOSTS


def _parts(uri: str) -> SplitResult | None:
    try:
        return urlsplit(uri)
    except ValueError:
        return None


def validate_redirect_uri(  # noqa: PLR0912 - one early exit per rejected form is the point
    uri: str, *, extra_schemes: Collection[str]
) -> str:
    """Return `uri` if it is an acceptable redirect URI, else raise `RedirectUriError`.

    The same checks run on a registration AND on the URI a client presents at /authorize, because
    parsers disagree about malformed URLs: Python reads `http://evil.com\\@localhost/cb` as host
    `localhost` with userinfo, a browser follows the backslash as a slash and goes to `evil.com`.
    So anything unusual - backslash, whitespace or control characters, userinfo, a fragment, a
    percent-encoded or upper-case loopback host, port 0 - is refused outright, not interpreted."""
    if (
        not uri
        or len(uri) > MAX_REDIRECT_URI_LENGTH
        or any(c.isspace() or not c.isprintable() for c in uri)
    ):
        raise RedirectUriError("redirect URI is empty, too long or contains whitespace")
    if "\\" in uri:
        raise RedirectUriError("redirect URI must not contain a backslash")
    if "*" in uri:
        raise RedirectUriError("redirect URI must not contain a wildcard")
    parts = _parts(uri)
    if parts is None:
        raise RedirectUriError("redirect URI is not a valid URL")
    scheme = parts.scheme  # urlsplit lower-cases it, so look at the text itself
    if not uri.startswith(f"{scheme}:"):
        raise RedirectUriError("redirect URI scheme must be lower case")
    if parts.fragment or "#" in uri:
        raise RedirectUriError("redirect URI must not contain a fragment")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise RedirectUriError("redirect URI must not contain credentials")
    if scheme in _FORBIDDEN_SCHEMES or _SCHEME.fullmatch(scheme) is None:
        raise RedirectUriError(f"redirect URI scheme {scheme!r} is not allowed")
    try:
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise RedirectUriError("redirect URI has an invalid host or port") from exc
    if port == 0:
        raise RedirectUriError("redirect URI port 0 is not allowed")
    if scheme == "https":
        if not host:
            raise RedirectUriError("redirect URI has no host")
        return uri
    if scheme == "http":
        if _LOOPBACK_NETLOC.fullmatch(parts.netloc) is None:
            raise RedirectUriError("http redirect URIs are only allowed for localhost")
        return uri
    if scheme in {s.lower() for s in extra_schemes}:
        return uri
    raise RedirectUriError(f"redirect URI scheme {scheme!r} is not allowed")


def validate_redirect_uris(uris: Iterable[str], *, extra_schemes: Collection[str]) -> list[str]:
    checked: list[str] = []
    for uri in uris:
        validate_redirect_uri(uri, extra_schemes=extra_schemes)
        if uri not in checked:
            checked.append(uri)
    if not checked:
        raise RedirectUriError("at least one redirect URI is required")
    if len(checked) > MAX_REDIRECT_URIS:
        raise RedirectUriError(f"at most {MAX_REDIRECT_URIS} redirect URIs may be registered")
    return checked


def _loopback_with_port(registered: str, port: int | None) -> str | None:
    """`registered` (a loopback http URI) rebuilt with `port` in place of its own, else None."""
    parts = _parts(registered)
    if parts is None or parts.scheme != "http" or _LOOPBACK_NETLOC.fullmatch(parts.netloc) is None:
        return None
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{port}" if port is not None else str(host)
    return urlunsplit(("http", netloc, parts.path, parts.query, ""))


def redirect_uri_matches(
    registered: Iterable[str], presented: str, *, extra_schemes: Collection[str]
) -> bool:
    """Whether `presented` is one of the `registered` redirect URIs.

    The presented URI must itself be a valid redirect URI (`validate_redirect_uri`). It then has
    to equal a registered one character for character - the single exception being a loopback
    http URI, whose port is free (RFC 8252 section 7.3): the registered URI is rebuilt with the
    presented port and the two strings must be identical. No URL parser decides a match."""
    try:
        validate_redirect_uri(presented, extra_schemes=extra_schemes)
        presented_parts = urlsplit(presented)
        port = presented_parts.port
    except (RedirectUriError, ValueError):
        return False
    for candidate in registered:
        if candidate == presented:
            return True
        if presented_parts.scheme == "http" and _loopback_with_port(candidate, port) == presented:
            return True
    return False


def redirect_host(uris: Iterable[str]) -> str:
    """The host to show a user on the consent screen for a client's redirect URIs: the first web
    one, else the scheme of the first custom-scheme one (`vscode://`)."""
    parsed = [p for p in (_parts(u) for u in uris) if p is not None]
    for parts in parsed:
        if parts.scheme.lower() in {"http", "https"} and parts.hostname:
            return parts.hostname.lower()
    for parts in parsed:
        if parts.scheme:
            return f"{parts.scheme.lower()}://"
    return ""


def resolve_scope(requested: str | None) -> str:
    """The scope to grant: the requested scopes this server supports, or the default.

    Unknown scopes (`offline_access`, `openid`, ...) are dropped rather than refused - clients
    send them speculatively, and the token response tells the client what it actually got."""
    wanted = [s for s in (requested or "").split() if s in SUPPORTED_SCOPES]
    unique = list(dict.fromkeys(wanted))
    return " ".join(unique) if unique else MCP_SCOPE_READ


def scope_is_subset(narrower: str, wider: str) -> bool:
    return set(narrower.split()) <= set(wider.split())


def same_resource(presented: str, canonical: str) -> bool:
    """RFC 8707 `resource` equals the MCP URL (a trailing slash is not a different resource)."""
    return presented.rstrip("/") == canonical.rstrip("/")


def is_public_cimd_url(client_id: str) -> bool:
    """A `client_id` that is a Client ID Metadata Document URL (https, a real path)."""
    parts = _parts(client_id)
    if parts is None or parts.scheme != "https" or not parts.hostname:
        return False
    if parts.username is not None or parts.password is not None or parts.fragment:
        return False
    if parts.path in {"", "/"} or any(seg in {".", ".."} for seg in parts.path.split("/")):
        return False
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return True
    return False  # an IP literal is never a client identity


__all__ = [
    "MAX_REDIRECT_URIS",
    "RedirectUriError",
    "is_loopback_host",
    "is_public_cimd_url",
    "is_valid_code_challenge",
    "redirect_host",
    "redirect_uri_matches",
    "resolve_scope",
    "s256_challenge",
    "same_resource",
    "scope_is_subset",
    "validate_redirect_uri",
    "validate_redirect_uris",
    "verify_pkce",
]
