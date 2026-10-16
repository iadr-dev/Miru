"""The pure rules of the MCP authorization server: PKCE, redirect URIs, scopes, resource match."""

from __future__ import annotations

import pytest

from accounts.mcp_oauth.rules import (
    RedirectUriError,
    is_public_cimd_url,
    is_valid_code_challenge,
    redirect_host,
    redirect_uri_matches,
    resolve_scope,
    s256_challenge,
    same_resource,
    scope_is_subset,
    validate_redirect_uri,
    validate_redirect_uris,
    verify_pkce,
)

SCHEMES = frozenset({"vscode", "cursor"})
# RFC 7636 appendix B test vector.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_s256_matches_the_rfc_7636_test_vector() -> None:
    assert s256_challenge(RFC_VERIFIER) == RFC_CHALLENGE
    assert verify_pkce(verifier=RFC_VERIFIER, challenge=RFC_CHALLENGE)


def test_pkce_rejects_a_wrong_or_malformed_verifier() -> None:
    assert not verify_pkce(verifier=RFC_VERIFIER[:-1] + "A", challenge=RFC_CHALLENGE)
    assert not verify_pkce(verifier="short", challenge=RFC_CHALLENGE)  # under 43 chars
    assert not verify_pkce(verifier=RFC_VERIFIER + "!", challenge=RFC_CHALLENGE)  # bad charset
    assert not verify_pkce(verifier="a" * 129, challenge=RFC_CHALLENGE)


def test_a_plain_challenge_is_not_an_s256_challenge() -> None:
    assert is_valid_code_challenge(RFC_CHALLENGE)
    assert not is_valid_code_challenge(RFC_VERIFIER + "x")  # wrong length
    assert not is_valid_code_challenge("")
    assert not is_valid_code_challenge("a" * 42 + "=")


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://www.cursor.com/agents/mcp/oauth/callback",
        "http://localhost:8787/callback",
        "http://127.0.0.1:33418",
        "http://[::1]:5000/cb",
        "http://localhost/callback",
        "vscode://vscode.github-authentication/did-authenticate",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
    ],
)
def test_acceptable_redirect_uris(uri: str) -> None:
    assert validate_redirect_uri(uri, extra_schemes=SCHEMES) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "http://example.com/cb",  # plain http off loopback
        "http://localhost.evil.com/cb",
        "http://127.0.0.1.evil.com/cb",
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "ftp://example.com/cb",
        "myapp://callback",  # custom scheme not on the allow-list
        "https://example.com/cb#frag",
        "https://user:pw@example.com/cb",
        "https://*.example.com/cb",
        "https:///nohost",
        "https://example.com/has space",
        "https://example.com:notaport/cb",
        "https://example.com/" + "a" * 2100,
    ],
)
def test_unacceptable_redirect_uris(uri: str) -> None:
    with pytest.raises(RedirectUriError):
        validate_redirect_uri(uri, extra_schemes=SCHEMES)


def test_a_registration_needs_at_least_one_and_at_most_ten_unique_uris() -> None:
    with pytest.raises(RedirectUriError):
        validate_redirect_uris([], extra_schemes=SCHEMES)
    assert validate_redirect_uris(
        ["https://a.example/cb", "https://a.example/cb"], extra_schemes=SCHEMES
    ) == ["https://a.example/cb"]
    with pytest.raises(RedirectUriError):
        validate_redirect_uris(
            [f"https://a{i}.example/cb" for i in range(11)], extra_schemes=SCHEMES
        )


def test_redirect_matching_is_exact_for_https() -> None:
    registered = ["https://claude.ai/api/mcp/auth_callback"]
    assert redirect_uri_matches(registered, "https://claude.ai/api/mcp/auth_callback")
    for presented in (
        "https://claude.ai/api/mcp/auth_callback/",
        "https://claude.ai/api/mcp/auth_callback?x=1",
        "https://claude.ai:444/api/mcp/auth_callback",
        "https://evil.example/api/mcp/auth_callback",
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callbac",
    ):
        assert not redirect_uri_matches(registered, presented), presented


def test_a_loopback_redirect_ignores_the_port_but_nothing_else() -> None:
    registered = ["http://localhost:8787/callback"]
    assert redirect_uri_matches(registered, "http://localhost:51234/callback")
    assert redirect_uri_matches(registered, "http://localhost/callback")
    assert redirect_uri_matches(["http://127.0.0.1/cb"], "http://127.0.0.1:9999/cb")
    assert redirect_uri_matches(["http://[::1]:1/cb"], "http://[::1]:2/cb")
    for presented in (
        "http://localhost:51234/other",  # path differs
        "http://localhost:51234/callback?x=1",  # query differs
        "http://127.0.0.1:51234/callback",  # a different loopback host name
        "https://localhost:51234/callback",  # scheme differs
        "http://localhost.evil.example:51234/callback",
    ):
        assert not redirect_uri_matches(registered, presented), presented


def test_the_port_rule_never_loosens_a_non_loopback_http_or_a_custom_scheme() -> None:
    assert not redirect_uri_matches(["https://a.example:1/cb"], "https://a.example:2/cb")
    assert not redirect_uri_matches(["vscode://x/y:1"], "vscode://x/y:2")


def test_an_unparsable_presented_uri_never_matches() -> None:
    assert not redirect_uri_matches(["http://localhost/cb"], "http://[::1/cb")


def test_the_consent_screen_host_is_the_first_web_redirect() -> None:
    assert redirect_host(["vscode://x/y", "https://claude.ai/cb"]) == "claude.ai"
    assert redirect_host(["http://localhost:8787/cb"]) == "localhost"
    assert redirect_host(["vscode://x/y"]) == "vscode://"
    assert redirect_host([]) == ""


def test_scope_resolution_keeps_supported_scopes_and_defaults_otherwise() -> None:
    assert resolve_scope(None) == "mcp:read"
    assert resolve_scope("") == "mcp:read"
    assert resolve_scope("mcp:read") == "mcp:read"
    assert resolve_scope("openid offline_access mcp:read mcp:read") == "mcp:read"
    assert resolve_scope("openid") == "mcp:read"
    assert scope_is_subset("mcp:read", "mcp:read")
    assert not scope_is_subset("mcp:read admin", "mcp:read")


def test_resource_match_tolerates_only_a_trailing_slash() -> None:
    canonical = "https://mcp.example.com/mcp"
    assert same_resource(canonical, canonical)
    assert same_resource(canonical + "/", canonical)
    assert not same_resource("https://mcp.example.com/other", canonical)
    assert not same_resource("https://mcp.example.com/mcp/x", canonical)
    assert not same_resource("https://evil.example.com/mcp", canonical)


@pytest.mark.parametrize(
    ("client_id", "expected"),
    [
        ("https://claude.ai/oauth/claude-code-client-metadata", True),
        ("https://app.example.com/client.json", True),
        ("https://app.example.com/", False),
        ("https://app.example.com", False),
        ("http://app.example.com/client.json", False),
        ("https://203.0.113.5/client.json", False),  # an IP literal is not an identity
        ("https://user@app.example.com/client.json", False),
        ("https://app.example.com/a/../client.json", False),
        ("https://app.example.com/client.json#x", False),
        ("0b9c6c52-7d3e-4b0b-8c37-6a52a8c2d6f1", False),
    ],
)
def test_which_client_ids_are_metadata_document_urls(client_id: str, expected: bool) -> None:
    assert is_public_cimd_url(client_id) is expected
