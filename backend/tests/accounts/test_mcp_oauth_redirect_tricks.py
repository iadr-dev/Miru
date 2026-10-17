"""Redirect-URI parse tricks: URLs that two parsers read differently.

The classic one: Python's `urlsplit("http://evil.com\\@localhost:1/callback")` reports host
`localhost` (with userinfo `evil.com\\`), a browser follows `\\` as `/` and goes to `evil.com`. The
presented redirect URI must be validated like a registration, and matched by rebuilding the
registered URI - never by comparing what a parser extracted from each.
"""

from __future__ import annotations

import pytest

from accounts.mcp_oauth.rules import (
    RedirectUriError,
    validate_redirect_uri,
)
from accounts.mcp_oauth.rules import redirect_uri_matches as _matches

SCHEMES = frozenset({"vscode", "cursor"})
BS = "\\"
REGISTERED_LOOPBACK = ["http://localhost/callback"]


def matches(registered: list[str], presented: str) -> bool:
    return _matches(registered, presented, extra_schemes=SCHEMES)


LOOPBACK_TRICKS = [
    # Python: host=localhost + userinfo. Browser: a path, destination evil.com.
    f"http://evil.com{BS}@localhost:1/callback",
    f"http://evil.com{BS}@localhost/callback",
    f"http://localhost{BS}@evil.com/callback",
    "http://evil.com@localhost:1/callback",
    "http://localhost:1@evil.com/callback",
    "http://localhost:pw@evil.com/callback",
    f"http://localhost/callback{BS}..{BS}x",
    "http://localhost/call\tback",
    "http://localhost/callback\n",
    "http://localhost/callback\r\nHost: evil.com",
    "http://local\thost/callback",
    "http://localhost/callback ",
    " http://localhost/callback",
    "http://localhost:1/callback\x00",
    "http://LOCALHOST:1/callback",
    "HTTP://localhost:1/callback",
    "Http://localhost:1/callback",
    "http://localhost.:1/callback",
    "http://localhost.evil.com:1/callback",
    "http://%6cocalhost:1/callback",
    "http://localhost%2eevil.com/callback",
    "http://localhost:0/callback",
    "http://localhost:65536/callback",
    "http://localhost:99999/callback",
    "http://localhost:-1/callback",
    "http://localhost:1:2/callback",
    "http://localhost:/callback",
    "http://[::1]:1/callback",
    "http://127.0.0.1:1/callback",
    "http://0.0.0.0:1/callback",
    "http://localhost:1/callback/..",
    "http://localhost:1/callback/../callback",
    "http://localhost:1/callback/",
    "http://localhost:1/Callback",
    "http://localhost:1/callback?x=1",
    "http://localhost:1/callback#frag",
    "http://localhost:1//callback",
    "http://localhost:1",
    "//localhost:1/callback",
    "localhost:1/callback",
    "https://localhost:1/callback",
    "http:localhost:1/callback",
    "http:///callback",
]


@pytest.mark.parametrize("presented", LOOPBACK_TRICKS)
def test_parse_tricks_never_match_a_loopback_registration(presented: str) -> None:
    assert not matches(REGISTERED_LOOPBACK, presented), repr(presented)


def test_the_port_rule_still_works_for_each_loopback_literal() -> None:
    assert matches(REGISTERED_LOOPBACK, "http://localhost:1/callback")
    assert matches(REGISTERED_LOOPBACK, "http://localhost:65535/callback")
    assert matches(["http://127.0.0.1/cb"], "http://127.0.0.1:8080/cb")
    assert matches(["http://[::1]/cb"], "http://[::1]:8080/cb")
    assert matches(["http://[::1]:5/cb?a=b"], "http://[::1]:8080/cb?a=b")
    assert not matches(["http://[::1]/cb"], "http://127.0.0.1:8080/cb")
    assert not matches(["http://127.0.0.1/cb"], "http://localhost:8080/cb")
    assert not matches(["http://localhost/cb?a=b"], "http://localhost:8080/cb?a=c")


HTTPS_TRICKS = [
    f"https://claude.ai{BS}@evil.com/api/mcp/auth_callback",
    f"https://evil.com{BS}.claude.ai/api/mcp/auth_callback",
    "https://claude.ai@evil.com/api/mcp/auth_callback",
    "HTTPS://claude.ai/api/mcp/auth_callback",
    "https://CLAUDE.AI/api/mcp/auth_callback",
    "https://claude.ai./api/mcp/auth_callback",
    "https://claude.ai/api/mcp/auth_callback\t",
    "https://claude.ai/api/mcp/auth_callback#x",
    "https://claude.ai:0/api/mcp/auth_callback",
]


@pytest.mark.parametrize("presented", HTTPS_TRICKS)
def test_parse_tricks_never_match_an_https_registration(presented: str) -> None:
    assert not matches(["https://claude.ai/api/mcp/auth_callback"], presented), repr(presented)


def test_a_bad_presented_uri_never_matches_even_if_a_registration_holds_the_same_string() -> None:
    bad = f"http://evil.com{BS}@localhost/callback"
    assert not matches([bad], bad)


REGISTRATION_TRICKS = [
    f"http://evil.com{BS}@localhost/cb",
    f"https://a.example/cb{BS}x",
    "HTTPS://a.example/cb",
    "http://LOCALHOST/cb",
    "http://localhost./cb",
    "http://%6cocalhost/cb",
    "http://localhost:0/cb",
    "https://a.example:0/cb",
    "http://user@localhost/cb",
]


@pytest.mark.parametrize("uri", REGISTRATION_TRICKS)
def test_registration_refuses_the_same_tricks(uri: str) -> None:
    with pytest.raises(RedirectUriError):
        validate_redirect_uri(uri, extra_schemes=SCHEMES)
