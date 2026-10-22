"""Characterization of `validate_redirect_uri`: the exact rejection reason for each form, the
order in which overlapping defects are reported, and every accepted shape. A reordered or loosened
check shows up here as a changed message or an accepted URI."""

from __future__ import annotations

import pytest

from accounts.mcp_oauth.rules import (
    MAX_REDIRECT_URI_LENGTH,
    RedirectUriError,
    redirect_uri_matches,
    validate_redirect_uri,
)

SCHEMES = frozenset({"vscode", "cursor"})
BS = "\\"

EMPTY = "redirect URI is empty, too long or contains whitespace"
BACKSLASH = "redirect URI must not contain a backslash"
WILDCARD = "redirect URI must not contain a wildcard"
INVALID = "redirect URI is not a valid URL"
LOWER = "redirect URI scheme must be lower case"
FRAGMENT = "redirect URI must not contain a fragment"
CREDS = "redirect URI must not contain credentials"
BAD_HOST_PORT = "redirect URI has an invalid host or port"
PORT0 = "redirect URI port 0 is not allowed"
NO_HOST = "redirect URI has no host"
HTTP_ONLY_LOCAL = "http redirect URIs are only allowed for localhost"


def not_allowed(scheme: str) -> str:
    return f"redirect URI scheme {scheme!r} is not allowed"


def reject(uri: str, *, extra: frozenset[str] = SCHEMES) -> str:
    with pytest.raises(RedirectUriError) as info:
        validate_redirect_uri(uri, extra_schemes=extra)
    return str(info.value)


REJECTED: list[tuple[str, str]] = [
    # empty / long / whitespace / control / non printable
    ("", EMPTY),
    ("https://a.example/" + "a" * MAX_REDIRECT_URI_LENGTH, EMPTY),
    ("https://a.example/has space", EMPTY),
    ("https://a.example/tab\there", EMPTY),
    ("https://a.example/new\nline", EMPTY),
    (" https://a.example/cb", EMPTY),
    ("https://a.example/cb ", EMPTY),
    ("https://a.example/\x01", EMPTY),
    ("https://a.example/\x00", EMPTY),
    ("https://a.example/\x7f", EMPTY),
    ("https://a.example/\u200b", EMPTY),
    ("https://a.example/\u00a0", EMPTY),
    ("https://a.example/\u2028", EMPTY),
    # whitespace is reported before a backslash, a wildcard or a bad URL
    (f"https://a.example/ {BS}", EMPTY),
    ("https://a.example/ *", EMPTY),
    # backslash before wildcard / url syntax
    (f"https://a.example{BS}cb", BACKSLASH),
    (f"http://evil.com{BS}@localhost/callback", BACKSLASH),
    (f"https://{BS}*", BACKSLASH),
    # wildcard before url syntax
    ("*", WILDCARD),
    ("https://*.example.com/cb", WILDCARD),
    ("https://a.example/*", WILDCARD),
    ("https://[*", WILDCARD),
    # urlsplit refuses it
    ("https://[::1/cb", INVALID),
    ("http://[bad/cb", INVALID),
    # scheme must be lower case in the text itself
    ("HTTPS://a.example/cb", LOWER),
    ("Http://localhost/cb", LOWER),
    ("VSCODE://x", LOWER),
    ("/relative/path", LOWER),
    ("a.example/cb", LOWER),
    ("1http://a.example/cb", LOWER),
    ("my_app://x", LOWER),
    # fragment before credentials and the scheme checks
    ("https://a.example/cb#x", FRAGMENT),
    ("https://a.example/cb#", FRAGMENT),
    ("vscode://x#y", FRAGMENT),
    ("https://u@a.example/cb#f", FRAGMENT),
    ("javascript:alert(1)#x", FRAGMENT),
    # credentials before the scheme and port checks
    ("https://user:pw@a.example/cb", CREDS),
    ("https://user@a.example/cb", CREDS),
    ("https://:pw@a.example/cb", CREDS),
    ("http://@localhost/cb", CREDS),
    ("vscode://u@x", CREDS),
    ("javascript://u@x", CREDS),
    ("ftp://u@a.example:0", CREDS),
    # forbidden and unknown schemes (forbidden wins even when allow-listed)
    ("javascript:alert(1)", not_allowed("javascript")),
    ("data:text/html,hi", not_allowed("data")),
    ("file:///etc/passwd", not_allowed("file")),
    ("vbscript:msgbox(1)", not_allowed("vbscript")),
    ("blob:https://a.example/x", not_allowed("blob")),
    ("about:blank", not_allowed("about")),
    ("ftp://a.example/cb", not_allowed("ftp")),
    ("ftp://a.example:0/cb", not_allowed("ftp")),
    ("ftp://a.example:abc/cb", not_allowed("ftp")),
    ("://x", not_allowed("")),
    ("myapp://callback", not_allowed("myapp")),
    ("custom+x.y-z://callback", not_allowed("custom+x.y-z")),
    # port / host parse problems
    ("https://a.example:notaport/cb", BAD_HOST_PORT),
    ("https://a.example:99999/cb", BAD_HOST_PORT),
    ("https://a.example:65536/cb", BAD_HOST_PORT),
    ("https://a.example:-1/cb", BAD_HOST_PORT),
    ("http://localhost:123456/cb", BAD_HOST_PORT),
    ("vscode://x:abc", BAD_HOST_PORT),
    ("https://a.example:1:2/cb", BAD_HOST_PORT),
    # port 0 is refused for every scheme, before the host / loopback checks
    ("https://a.example:0/cb", PORT0),
    ("https://:0/cb", PORT0),
    ("http://localhost:0/cb", PORT0),
    ("http://[::1]:0/cb", PORT0),
    ("http://example.com:0/cb", PORT0),
    ("vscode://x:0", PORT0),
    # https needs a host
    ("https:///nohost", NO_HOST),
    ("https://:443/cb", NO_HOST),
    ("https:", NO_HOST),
    ("https:/path-only", NO_HOST),
    # http only for the exact loopback spellings
    ("http://example.com/cb", HTTP_ONLY_LOCAL),
    ("http://localhost.evil.com/cb", HTTP_ONLY_LOCAL),
    ("http://127.0.0.1.evil.com/cb", HTTP_ONLY_LOCAL),
    ("http://LOCALHOST/cb", HTTP_ONLY_LOCAL),
    ("http://localhost./cb", HTTP_ONLY_LOCAL),
    ("http://localhost:/cb", HTTP_ONLY_LOCAL),
    ("http://%6cocalhost/cb", HTTP_ONLY_LOCAL),
    ("http://localhost%2eevil.com/cb", HTTP_ONLY_LOCAL),
    ("http://0.0.0.0/cb", HTTP_ONLY_LOCAL),
    ("http://[::2]/cb", HTTP_ONLY_LOCAL),
    ("http://[0:0:0:0:0:0:0:1]/cb", HTTP_ONLY_LOCAL),
    ("http://127.1/cb", HTTP_ONLY_LOCAL),
    ("http:///cb", HTTP_ONLY_LOCAL),
    ("http:", HTTP_ONLY_LOCAL),
]


@pytest.mark.parametrize(("uri", "message"), REJECTED)
def test_each_defect_is_refused_with_its_reason(uri: str, message: str) -> None:
    assert reject(uri) == message


ACCEPTED = [
    "https://a.example",
    "https://a.example/cb",
    "https://a.example:8443/cb?x=1&y=2",
    "https://a.example:/cb",
    "https://xn--bcher-kva.example/cb",
    "https://192.0.2.1/cb",
    "https://[2001:db8::1]/cb",
    "https://a.example/x@y",
    "https://a.example/caf\u00e9",
    "https://a.example/" + "a" * (MAX_REDIRECT_URI_LENGTH - len("https://a.example/")),
    "http://localhost",
    "http://localhost/callback",
    "http://localhost:80/x",
    "http://localhost:00001/x",
    "http://localhost:65535/x",
    "http://127.0.0.1",
    "http://127.0.0.1:33418",
    "http://[::1]",
    "http://[::1]:5000/cb",
    "vscode://vscode.github-authentication/did-authenticate",
    "cursor://anysphere.cursor-retrieval/oauth/callback",
    "vscode:callback",
    "vscode:/callback",
    "vscode://host:8080/cb",
]


@pytest.mark.parametrize("uri", ACCEPTED)
def test_acceptable_forms_are_returned_unchanged(uri: str) -> None:
    assert validate_redirect_uri(uri, extra_schemes=SCHEMES) == uri


def test_exactly_the_maximum_length_is_accepted_and_one_more_is_not() -> None:
    base = "https://a.example/"
    at_limit = base + "a" * (MAX_REDIRECT_URI_LENGTH - len(base))
    assert len(at_limit) == MAX_REDIRECT_URI_LENGTH
    assert validate_redirect_uri(at_limit, extra_schemes=SCHEMES) == at_limit
    assert reject(at_limit + "a") == EMPTY


def test_custom_schemes_only_come_from_the_allow_list_case_insensitively() -> None:
    assert reject("vscode://x", extra=frozenset()) == not_allowed("vscode")
    assert reject("myapp://x", extra=frozenset({"vscode"})) == not_allowed("myapp")
    assert validate_redirect_uri("myapp://x", extra_schemes=["MyApp"]) == "myapp://x"
    assert validate_redirect_uri("myapp://x", extra_schemes={"MYAPP", "other"}) == "myapp://x"
    assert validate_redirect_uri("myapp://x", extra_schemes=("a", "myapp")) == "myapp://x"


@pytest.mark.parametrize(
    "scheme", ["javascript", "data", "file", "vbscript", "blob", "about", "ftp"]
)
def test_a_forbidden_scheme_is_refused_even_when_the_operator_allow_lists_it(scheme: str) -> None:
    uri = f"{scheme}://x/cb"
    assert reject(uri, extra=frozenset({scheme})) == not_allowed(scheme)


def test_an_allow_listed_https_or_http_cannot_widen_the_built_in_rules() -> None:
    extra = frozenset({"http", "https"})
    assert reject("http://example.com/cb", extra=extra) == HTTP_ONLY_LOCAL
    assert reject("https:///x", extra=extra) == NO_HOST


@pytest.mark.parametrize(
    "presented",
    [uri for uri, _ in REJECTED],
)
def test_a_rejected_uri_never_matches_even_when_registered_verbatim(presented: str) -> None:
    assert not redirect_uri_matches([presented], presented, extra_schemes=SCHEMES)


@pytest.mark.parametrize("presented", ACCEPTED)
def test_an_accepted_uri_matches_itself(presented: str) -> None:
    assert redirect_uri_matches([presented], presented, extra_schemes=SCHEMES)
