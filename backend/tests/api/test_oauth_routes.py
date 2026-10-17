"""The MCP sign-in authorization server over HTTP: metadata, authorize, consent, token, register,
revoke - the exact bytes an MCP client and the web app's consent screen exchange with the API.

A real app over a real (SQLite) database; only the Client ID Metadata Document fetch is replaced
(its own tests cover the fetcher), because a test has no public host to fetch from.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from accounts.mcp_tokens import verify_mcp_access_token
from api.routers.auth import safe_next_path
from api.routers.oauth import get_metadata_source
from config.settings import Settings
from dataaccess.oauth_client_metadata import ClientMetadataFetchError
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

ISSUER = "https://api.example.com"
RESOURCE = "https://mcp.example.com/mcp"
WEB = "https://web.example.com"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=")
CHALLENGE_S = CHALLENGE.decode()
CALLBACK = "http://localhost:8787/callback"
FORM = {"content-type": "application/x-www-form-urlencoded"}
CIMD_URL = "https://claude.ai/oauth/claude-code-client-metadata"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "jwt_secret_key": _TEST_JWT_SECRET,
        "env": "development",
        "oauth_issuer": ISSUER,
        "mcp_public_base_url": "https://mcp.example.com",
        "frontend_base_url": WEB,
        "voice_stt_provider": "stub",
        "voice_tts_provider": "stub",
        "google_client_id": "g",
        "google_client_secret": "gs",
        "google_redirect_uri": "http://testserver/api/v1/auth/oauth/google/callback",
        **overrides,
    }
    return Settings(**base)  # type: ignore[arg-type]


class FakeMetadataSource:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, object]] = {}
        self.error: ClientMetadataFetchError | None = None

    async def fetch(self, url: str) -> dict[str, object]:
        if self.error is not None:
            raise self.error
        return self.documents[url]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def metadata_source() -> FakeMetadataSource:
    return FakeMetadataSource()


@pytest.fixture
def client(settings: Settings, metadata_source: FakeMetadataSource) -> Iterator[TestClient]:
    with build_test_client(settings, agent_answer="x") as test_client:
        test_client.app.dependency_overrides[get_metadata_source] = lambda: metadata_source  # type: ignore[attr-defined]
        yield test_client


def bearer(client: TestClient, settings: Settings, tier: Tier = Tier.FREE) -> dict[str, str]:
    headers, _ = sign_in(client, settings, tier=tier)
    return headers


def register(client: TestClient, redirect_uri: str = CALLBACK) -> str:
    resp = client.post(
        "/oauth/register", json={"client_name": "Test client", "redirect_uris": [redirect_uri]}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["client_id"])


def authorize_params(cid: str, /, **overrides: str | None) -> dict[str, str]:
    params: dict[str, str | None] = {
        "client_id": cid,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": CHALLENGE_S,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": "mcp:read",
        "state": "st-1",
        **overrides,
    }
    return {k: v for k, v in params.items() if v is not None}


def authorize(client: TestClient, cid: str, /, **overrides: str | None):
    return client.get(
        "/oauth/authorize",
        params=authorize_params(cid, **overrides),
        follow_redirects=False,
    )


def request_id_from(location: str) -> str:
    return parse_qs(urlsplit(location).query)["request"][0]


def approve(client: TestClient, headers: dict[str, str], request_id: str) -> str:
    consent = client.get(f"/api/v1/oauth/consent/{request_id}", headers=headers)
    assert consent.status_code == 200, consent.text
    done = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        json={"decision": "approve", "nonce": consent.json()["nonce"]},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    return str(done.json()["redirect_to"])


def code_from(redirect_to: str) -> str:
    return parse_qs(urlsplit(redirect_to).query)["code"][0]


def token_request(client: TestClient, **fields: str | None):
    data = {k: v for k, v in fields.items() if v is not None}
    return client.post("/oauth/token", content=urlencode(data), headers=FORM)


def signed_in_flow(client: TestClient, settings: Settings) -> tuple[str, dict[str, str], dict]:
    """Register -> authorize -> approve -> token. Returns (client_id, headers, token response)."""
    headers = bearer(client, settings)
    client_id = register(client)
    location = authorize(client, client_id).headers["location"]
    code = code_from(approve(client, headers, request_id_from(location)))
    resp = token_request(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=VERIFIER,
        resource=RESOURCE,
    )
    assert resp.status_code == 200, resp.text
    return client_id, headers, resp.json()


# ----------------------------------------------------------------------- metadata


def test_authorization_server_metadata_advertises_exactly_what_is_implemented(
    client: TestClient,
) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "max-age=300"
    assert resp.json() == {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/oauth/authorize",
        "token_endpoint": f"{ISSUER}/oauth/token",
        "registration_endpoint": f"{ISSUER}/oauth/register",
        "revocation_endpoint": f"{ISSUER}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp:read"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": "https://modelcontextprotocol.io/specification/",
    }


def test_an_issuer_with_a_trailing_slash_is_normalized() -> None:
    with build_test_client(make_settings(oauth_issuer=ISSUER + "/"), agent_answer="x") as c:
        assert c.get("/.well-known/oauth-authorization-server").json()["issuer"] == ISSUER


def test_without_an_issuer_the_server_says_so_instead_of_pretending() -> None:
    with build_test_client(make_settings(oauth_issuer=""), agent_answer="x") as c:
        assert c.get("/.well-known/oauth-authorization-server").status_code == 404
        assert c.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 503
        assert (
            c.post("/oauth/token", content="grant_type=refresh_token", headers=FORM).status_code
            == 503
        )
        assert c.get("/oauth/authorize", follow_redirects=False).status_code == 503


# ----------------------------------------------------------------------- authorize


def test_authorize_sends_the_browser_to_the_web_consent_screen_without_needing_a_session(
    client: TestClient,
) -> None:
    client_id = register(client)
    resp = authorize(client, client_id)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{WEB}/zh-TW/oauth/consent?request=")
    assert request_id_from(location)
    english = client.get(
        "/oauth/authorize",
        params=authorize_params(client_id),
        headers={"accept-language": "en-US,en;q=0.9"},
        follow_redirects=False,
    )
    assert english.headers["location"].startswith(f"{WEB}/en/oauth/consent?request=")


@pytest.mark.parametrize(
    "overrides",
    [
        {"redirect_uri": "https://evil.example/callback"},
        {"redirect_uri": "http://localhost:8787/other"},
        {"redirect_uri": "javascript:alert(1)"},
        {"client_id": "does-not-exist"},
        {"client_id": "https://10.0.0.5/client.json"},
    ],
)
def test_a_bad_client_or_redirect_uri_is_shown_to_the_user_never_redirected_to(
    client: TestClient, overrides: dict[str, str]
) -> None:
    client_id = register(client)
    resp = authorize(client, client_id, **overrides)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{WEB}/zh-TW/oauth/consent?error=")
    assert "evil.example" not in location
    assert "javascript" not in location


def test_authorize_without_a_client_id_is_shown_as_an_error(client: TestClient) -> None:
    resp = client.get("/oauth/authorize", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{WEB}/zh-TW/oauth/consent?error=invalid_request"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
    ],
)
def test_other_defects_go_back_to_the_client_with_state_and_issuer(
    client: TestClient, overrides: dict[str, str | None], error: str
) -> None:
    client_id = register(client)
    resp = authorize(client, client_id, **overrides)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(CALLBACK + "?")
    params = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["location"]).query).items()}
    assert params["error"] == error
    assert params["state"] == "st-1"
    assert params["iss"] == ISSUER
    assert params["error_description"]


def test_a_metadata_document_client_goes_through_authorize(
    client: TestClient, metadata_source: FakeMetadataSource
) -> None:
    metadata_source.documents[CIMD_URL] = {
        "client_id": CIMD_URL,
        "client_name": "Claude Code",
        "redirect_uris": ["http://localhost/callback"],
        "token_endpoint_auth_method": "none",
    }
    resp = authorize(client, CIMD_URL, redirect_uri="http://localhost:50123/callback")
    assert resp.headers["location"].startswith(f"{WEB}/zh-TW/oauth/consent?request=")
    metadata_source.error = ClientMetadataFetchError("blocked_address")
    other = "https://other.example/client.json"
    refused = authorize(client, other, redirect_uri="http://localhost:50123/callback")
    assert "error=invalid_client" in refused.headers["location"]


# ------------------------------------------------------------ consent (the web app's calls)


def test_the_consent_screen_needs_a_signed_in_user(client: TestClient) -> None:
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    assert client.get(f"/api/v1/oauth/consent/{request_id}").status_code == 401
    posted = client.post(
        f"/api/v1/oauth/consent/{request_id}", json={"decision": "approve", "nonce": "x"}
    )
    assert posted.status_code == 401


def test_the_consent_response_shape_and_the_session_cookie(
    client: TestClient, settings: Settings
) -> None:
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    token = bearer(client, settings)["Authorization"].removeprefix("Bearer ")
    client.cookies.set("access_token", token)  # the web app authenticates with its cookie
    resp = client.get(f"/api/v1/oauth/consent/{request_id}")
    client.cookies.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "request_id",
        "client",
        "redirect_uri",
        "scopes",
        "resource",
        "nonce",
        "expires_at",
    }
    assert body["redirect_uri"] == CALLBACK
    assert body["request_id"] == request_id
    assert body["client"] == {
        "name": "Test client",
        "client_id": body["client"]["client_id"],
        "kind": "registered",
        "redirect_host": "localhost",
        "verified": False,
    }
    assert body["scopes"] == [{"id": "mcp:read", "description": body["scopes"][0]["description"]}]
    assert body["resource"] == {"url": RESOURCE, "label": "Miru MCP"}
    assert len(body["nonce"]) >= 24


def test_approving_returns_the_redirect_with_a_one_time_code(
    client: TestClient, settings: Settings
) -> None:
    headers = bearer(client, settings)
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    redirect_to = approve(client, headers, request_id)
    parts = urlsplit(redirect_to)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == CALLBACK
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert set(params) == {"code", "state", "iss"}
    assert (params["state"], params["iss"]) == ("st-1", ISSUER)


def test_denying_returns_access_denied(client: TestClient, settings: Settings) -> None:
    headers = bearer(client, settings)
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    nonce = client.get(f"/api/v1/oauth/consent/{request_id}", headers=headers).json()["nonce"]
    done = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        json={"decision": "deny", "nonce": nonce},
        headers=headers,
    )
    assert done.status_code == 200
    params = {k: v[0] for k, v in parse_qs(urlsplit(done.json()["redirect_to"]).query).items()}
    assert params["error"] == "access_denied"
    assert "code" not in params


def test_a_decision_needs_the_nonce_only_the_consent_page_can_read(
    client: TestClient, settings: Settings
) -> None:
    headers = bearer(client, settings)
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    for bad in ("", "guess"):
        resp = client.post(
            f"/api/v1/oauth/consent/{request_id}",
            json={"decision": "approve", "nonce": bad},
            headers=headers,
        )
        assert resp.status_code in {403, 422}
    forged = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        json={"decision": "approve", "nonce": "guess"},
        headers=headers,
    )
    assert forged.status_code == 403
    assert forged.json()["code"] == "oauth_nonce_mismatch"
    # A cross-site HTML form can only send form encoding, which this endpoint does not accept.
    form = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        content="decision=approve&nonce=guess",
        headers={**headers, **FORM},
    )
    assert form.status_code == 422
    assert approve(client, headers, request_id)  # the legitimate page still works afterwards


def test_a_request_is_answered_once_and_unknown_ids_are_404(
    client: TestClient, settings: Settings
) -> None:
    headers = bearer(client, settings)
    request_id = request_id_from(authorize(client, register(client)).headers["location"])
    nonce = client.get(f"/api/v1/oauth/consent/{request_id}", headers=headers).json()["nonce"]
    body = {"decision": "approve", "nonce": nonce}
    first = client.post(f"/api/v1/oauth/consent/{request_id}", json=body, headers=headers)
    assert first.status_code == 200
    again = client.post(f"/api/v1/oauth/consent/{request_id}", json=body, headers=headers)
    assert (again.status_code, again.json()["code"]) == (404, "oauth_request_not_found")
    assert client.get("/api/v1/oauth/consent/nope", headers=headers).status_code == 404


# ----------------------------------------------------------------------- token


def test_the_authorization_code_flow_returns_tokens_for_this_resource(
    client: TestClient, settings: Settings
) -> None:
    _, _, tokens = signed_in_flow(client, settings)
    assert set(tokens) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 600
    assert tokens["scope"] == "mcp:read"
    claims = verify_mcp_access_token(
        tokens["access_token"], settings=settings, issuer=ISSUER, resource=RESOURCE
    )
    assert claims.scope == "mcp:read"


def test_token_responses_are_not_cacheable(client: TestClient, settings: Settings) -> None:
    client_id = register(client)
    headers = bearer(client, settings)
    code = code_from(
        approve(
            client,
            headers,
            request_id_from(authorize(client, client_id).headers["location"]),
        )
    )
    ok = token_request(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=VERIFIER,
    )
    bad = token_request(client, grant_type="authorization_code", code="x", client_id=client_id)
    for resp in (ok, bad):
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["pragma"] == "no-cache"


def test_a_code_cannot_be_redeemed_twice(client: TestClient, settings: Settings) -> None:
    client_id = register(client)
    headers = bearer(client, settings)
    code = code_from(
        approve(client, headers, request_id_from(authorize(client, client_id).headers["location"]))
    )
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": client_id,
        "code_verifier": VERIFIER,
    }
    first = token_request(client, **fields)
    second = token_request(client, **fields)
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
    # The first redemption's refresh token died with the replayed code.
    refresh = token_request(
        client,
        grant_type="refresh_token",
        refresh_token=first.json()["refresh_token"],
        client_id=client_id,
    )
    assert refresh.json()["error"] == "invalid_grant"


def test_token_errors_are_rfc_6749_json(client: TestClient, settings: Settings) -> None:
    client_id = register(client)
    cases = [
        ({"grant_type": "password", "client_id": client_id}, "unsupported_grant_type"),
        ({"client_id": client_id}, "unsupported_grant_type"),
        ({"grant_type": "authorization_code", "client_id": client_id}, "invalid_request"),
        ({"grant_type": "refresh_token", "client_id": client_id}, "invalid_request"),
        ({"grant_type": "authorization_code", "code": "x"}, "invalid_client"),
        (
            {"grant_type": "authorization_code", "code": "x", "client_id": client_id},
            "invalid_grant",
        ),
        (
            {"grant_type": "refresh_token", "refresh_token": "x", "client_id": client_id},
            "invalid_grant",
        ),
    ]
    for fields, error in cases:
        resp = token_request(client, **fields)
        assert resp.status_code == 400, fields
        assert resp.json()["error"] == error, fields
        assert resp.json()["error_description"]
    del settings


def test_the_token_endpoint_accepts_form_encoding_only_and_one_value_per_parameter(
    client: TestClient,
) -> None:
    as_json = client.post("/oauth/token", json={"grant_type": "authorization_code"})
    assert as_json.status_code == 400
    assert as_json.json()["error"] == "invalid_request"
    repeated = client.post(
        "/oauth/token",
        content="grant_type=refresh_token&grant_type=authorization_code&client_id=a",
        headers=FORM,
    )
    assert repeated.json()["error"] == "invalid_request"
    huge = client.post("/oauth/token", content="a=" + "x" * 20_000, headers=FORM)
    assert huge.status_code == 413


def test_pkce_failure_and_resource_mismatch_are_refused(
    client: TestClient, settings: Settings
) -> None:
    client_id = register(client)
    headers = bearer(client, settings)
    for fields, error in (
        ({"code_verifier": "A" * 43}, "invalid_grant"),
        ({"resource": "https://evil.example/mcp"}, "invalid_target"),
    ):
        code = code_from(
            approve(
                client, headers, request_id_from(authorize(client, client_id).headers["location"])
            )
        )
        resp = token_request(
            client,
            **{
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CALLBACK,
                "client_id": client_id,
                "code_verifier": VERIFIER,
                **fields,
            },
        )
        assert resp.json()["error"] == error


def test_refresh_rotates_and_a_replayed_token_revokes_the_grant(
    client: TestClient, settings: Settings
) -> None:
    client_id, _, first = signed_in_flow(client, settings)

    def refresh(token: str):
        return token_request(
            client,
            grant_type="refresh_token",
            refresh_token=token,
            client_id=client_id,
            resource=RESOURCE,
        )

    second = refresh(first["refresh_token"])
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    replay = refresh(first["refresh_token"])
    assert (replay.status_code, replay.json()["error"]) == (400, "invalid_grant")
    after = refresh(second.json()["refresh_token"])
    assert after.json()["error"] == "invalid_grant"  # the legitimate holder lost the grant too


def test_the_token_endpoint_is_rate_limited_per_address() -> None:
    with build_test_client(make_settings(oauth_token_per_minute=3), agent_answer="x") as c:
        codes = [
            c.post("/oauth/token", content="grant_type=x&client_id=a", headers=FORM)
            for _ in range(5)
        ]
        assert [r.status_code for r in codes] == [400, 400, 400, 429, 429]
        assert codes[3].json()["error"] == "temporarily_unavailable"
        assert int(codes[3].headers["retry-after"]) >= 1


# ----------------------------------------------------------------------- register (RFC 7591)


def test_registration_returns_the_registered_public_client(client: TestClient) -> None:
    resp = client.post(
        "/oauth/register",
        json={
            "client_name": "My App",
            "redirect_uris": [CALLBACK, "https://app.example/cb"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "mcp:read",
            "software_id": "ignored-member",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {
        "client_id",
        "client_id_issued_at",
        "client_name",
        "redirect_uris",
        "token_endpoint_auth_method",
        "grant_types",
        "response_types",
        "scope",
    }
    assert body["client_name"] == "My App"
    assert body["redirect_uris"] == [CALLBACK, "https://app.example/cb"]
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body
    assert resp.headers["cache-control"] == "no-store"


_OK = ["https://a.example/cb"]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"redirect_uris": ["http://example.com/cb"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["javascript:alert(1)"]}, "invalid_redirect_uri"),
        ({"redirect_uris": []}, "invalid_redirect_uri"),
        ({}, "invalid_redirect_uri"),
        (
            {"redirect_uris": _OK, "token_endpoint_auth_method": "client_secret_basic"},
            "invalid_client_metadata",
        ),
        (
            {"redirect_uris": _OK, "grant_types": ["client_credentials"]},
            "invalid_client_metadata",
        ),
        ({"redirect_uris": _OK, "response_types": ["token"]}, "invalid_client_metadata"),
        ({"redirect_uris": "https://a.example/cb"}, "invalid_client_metadata"),
        ({"redirect_uris": _OK, "client_name": "x" * 300}, "invalid_client_metadata"),
    ],
)
def test_invalid_registrations_are_rfc_7591_errors(
    client: TestClient, payload: dict[str, object], error: str
) -> None:
    resp = client.post("/oauth/register", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == error


def test_registration_rejects_a_body_that_is_not_a_json_object(client: TestClient) -> None:
    for content in (b"not json", b"[1]", b"null", b""):
        resp = client.post(
            "/oauth/register", content=content, headers={"content-type": "application/json"}
        )
        assert resp.status_code == 400, content
        assert resp.json()["error"] == "invalid_client_metadata"
    big = client.post("/oauth/register", content=b" " * 20_000)
    assert big.status_code == 413


def test_registration_is_rate_limited_per_address() -> None:
    with build_test_client(make_settings(oauth_register_per_hour=2), agent_answer="x") as c:
        codes = [
            c.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code
            for _ in range(4)
        ]
        assert codes == [201, 201, 429, 429]


def test_the_number_of_registered_clients_is_capped() -> None:
    with build_test_client(make_settings(oauth_max_registered_clients=1), agent_answer="x") as c:
        assert c.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 201
        full = c.post("/oauth/register", json={"redirect_uris": [CALLBACK]})
        assert full.status_code == 503
        assert full.json()["error"] == "temporarily_unavailable"


# ----------------------------------------------------------------------- revoke (RFC 7009)


def test_revoking_a_refresh_token_ends_the_grant(client: TestClient, settings: Settings) -> None:
    client_id, _, tokens = signed_in_flow(client, settings)
    revoked = client.post(
        "/oauth/revoke",
        content=urlencode(
            {
                "token": tokens["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": client_id,
            }
        ),
        headers=FORM,
    )
    assert (revoked.status_code, revoked.json()) == (200, {})
    refresh = token_request(
        client,
        grant_type="refresh_token",
        refresh_token=tokens["refresh_token"],
        client_id=client_id,
    )
    assert refresh.json()["error"] == "invalid_grant"


def test_revoking_an_access_token_ends_the_grant_too(
    client: TestClient, settings: Settings
) -> None:
    client_id, _, tokens = signed_in_flow(client, settings)
    revoked = client.post(
        "/oauth/revoke", content=urlencode({"token": tokens["access_token"]}), headers=FORM
    )
    assert revoked.status_code == 200
    refresh = token_request(
        client,
        grant_type="refresh_token",
        refresh_token=tokens["refresh_token"],
        client_id=client_id,
    )
    assert refresh.json()["error"] == "invalid_grant"


def test_revoking_an_unknown_token_is_still_200(client: TestClient) -> None:
    resp = client.post("/oauth/revoke", content="token=never-issued", headers=FORM)
    assert (resp.status_code, resp.json()) == (200, {})
    missing = client.post("/oauth/revoke", content="x=1", headers=FORM)
    assert missing.status_code == 400
    assert missing.json()["error"] == "invalid_request"


# ------------------------------------------------------------ back to consent after sign-in


@pytest.mark.parametrize(
    "value",
    [
        "/zh-TW/oauth/consent?request=abc-123",
        "/en/oauth/consent?request=0b9c6c52",
        "/chat",
    ],
)
def test_safe_next_paths_are_kept(value: str) -> None:
    assert safe_next_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "//evil.example/x",
        "https://evil.example/x",
        "javascript:alert(1)",
        "/\\evil.example",
        "/ok\\..\\evil",
        "evil.example/x",
        "/path\nSet-Cookie: x=1",
        "/" + "a" * 600,
    ],
)
def test_unsafe_next_values_are_dropped(value: str | None) -> None:
    assert safe_next_path(value) is None


def test_sign_in_remembers_a_safe_next_path_in_a_flow_cookie(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/oauth/google", params={"next": "/en/oauth/consent?request=abc"}
    )
    assert resp.status_code == 200
    assert (client.cookies.get("oauth_next") or "").strip('"') == "/en/oauth/consent?request=abc"


def test_sign_in_ignores_an_unsafe_next_path(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/oauth/google", params={"next": "https://evil.example/"})
    assert resp.status_code == 200
    assert client.cookies.get("oauth_next") is None
