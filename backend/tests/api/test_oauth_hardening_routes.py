"""Review fixes at the HTTP edge: browser/user-bound consent, streaming size caps, IPv6 rate keys,
global registration limit, metadata-fetch metering, optional redirect_uri at /token."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.rate_guard import rate_limit_address
from api.routers.oauth import get_metadata_source
from config.settings import Settings
from tests.api.conftest import build_test_client
from tests.api.test_oauth_routes import (
    CALLBACK,
    FORM,
    RESOURCE,
    VERIFIER,
    FakeMetadataSource,
    approve,
    authorize,
    bearer,
    code_from,
    make_settings,
    register,
    request_id_from,
    token_request,
)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def source() -> FakeMetadataSource:
    return FakeMetadataSource()


@pytest.fixture
def client(settings: Settings, source: FakeMetadataSource) -> Iterator[TestClient]:
    with build_test_client(settings, agent_answer="x") as c:
        c.app.dependency_overrides[get_metadata_source] = lambda: source  # type: ignore[attr-defined]
        yield c


def start(client: TestClient) -> str:
    return request_id_from(authorize(client, register(client)).headers["location"])


def test_authorize_sets_an_http_only_lax_cookie_scoped_to_the_request(client: TestClient) -> None:
    resp = authorize(client, register(client))
    request_id = request_id_from(resp.headers["location"])
    header = resp.headers["set-cookie"]
    assert header.startswith(f"oauth_req_{request_id}=")
    lowered = header.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "domain=" not in lowered  # host-only on the API origin
    assert "max-age=600" in lowered


def test_consent_needs_the_browser_that_started_the_flow(
    client: TestClient, settings: Settings
) -> None:
    headers = bearer(client, settings)
    request_id = start(client)
    client.cookies.clear()  # another browser: it never received the cookie
    seen = client.get(f"/api/v1/oauth/consent/{request_id}", headers=headers)
    assert (seen.status_code, seen.json()["code"]) == (403, "oauth_browser_mismatch")
    posted = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        json={"decision": "approve", "nonce": "x"},
        headers=headers,
    )
    assert (posted.status_code, posted.json()["code"]) == (403, "oauth_browser_mismatch")
    client.cookies.set(f"oauth_req_{request_id}", "guessed-secret")
    wrong = client.get(f"/api/v1/oauth/consent/{request_id}", headers=headers)
    assert wrong.json()["code"] == "oauth_browser_mismatch"


def test_consent_belongs_to_the_first_user_who_opened_it(
    client: TestClient, settings: Settings
) -> None:
    owner, other = bearer(client, settings), bearer(client, settings)
    request_id = start(client)
    shown = client.get(f"/api/v1/oauth/consent/{request_id}", headers=owner)
    assert shown.status_code == 200
    stolen = client.get(f"/api/v1/oauth/consent/{request_id}", headers=other)
    assert (stolen.status_code, stolen.json()["code"]) == (403, "oauth_user_mismatch")
    decide = client.post(
        f"/api/v1/oauth/consent/{request_id}",
        json={"decision": "approve", "nonce": shown.json()["nonce"]},
        headers=other,
    )
    assert decide.status_code == 403
    assert approve(client, owner, request_id)  # the owner is unaffected


def test_a_chunked_body_is_cut_off_at_the_cap_without_a_content_length(
    client: TestClient,
) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(100):
            yield b"a=" + b"x" * 1000 + b"&"

    form = client.post("/oauth/token", content=chunks(), headers=FORM)
    assert form.status_code == 413
    registered = client.post(
        "/oauth/register", content=chunks(), headers={"content-type": "application/json"}
    )
    assert registered.status_code == 413
    revoked = client.post("/oauth/revoke", content=chunks(), headers=FORM)
    assert revoked.status_code == 413


def test_a_declared_oversize_length_is_refused_before_reading(client: TestClient) -> None:
    resp = client.post("/oauth/token", content=b"x", headers={**FORM, "content-length": "999999"})
    assert resp.status_code in {400, 413}


def test_ipv6_clients_are_counted_by_their_slash_64() -> None:
    assert rate_limit_address("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == "2001:db8:1:2::/64"
    assert rate_limit_address("2001:db8:1:2::1") == rate_limit_address("2001:db8:1:2:ffff::9")
    assert rate_limit_address("2001:db8:1:3::1") != rate_limit_address("2001:db8:1:2::1")
    assert rate_limit_address("203.0.113.7") == "203.0.113.7"
    assert rate_limit_address("::ffff:203.0.113.7") == "::ffff:203.0.113.7"
    assert rate_limit_address("unknown") == "unknown"


def test_registration_has_a_global_limit_across_callers() -> None:
    settings = make_settings(
        oauth_register_per_hour=100, oauth_register_global_per_hour=3, trusted_proxy_count=1
    )
    with build_test_client(settings, agent_answer="x") as c:
        codes = [
            c.post(
                "/oauth/register",
                json={"redirect_uris": [CALLBACK]},
                headers={"x-forwarded-for": f"198.51.100.{i}"},
            ).status_code
            for i in range(5)
        ]
    assert codes == [201, 201, 201, 429, 429]


def test_new_metadata_documents_are_metered_per_address(source: FakeMetadataSource) -> None:
    settings = make_settings(oauth_cimd_fetch_per_minute=2)
    with build_test_client(settings, agent_answer="x") as c:
        c.app.dependency_overrides[get_metadata_source] = lambda: source  # type: ignore[attr-defined]
        urls = [f"https://app{i}.example.com/client.json" for i in range(4)]
        for url in urls:
            source.documents[url] = {
                "client_id": url,
                "redirect_uris": ["http://localhost/callback"],
            }
        outcomes = [
            authorize(c, url, redirect_uri="http://localhost:1/callback").headers["location"]
            for url in urls
        ]
    assert ["request=" in o for o in outcomes] == [True, True, False, False]
    assert "temporarily_unavailable" in outcomes[2]


def test_the_token_request_may_omit_redirect_uri_when_the_authorization_did(
    client: TestClient, settings: Settings
) -> None:
    headers = bearer(client, settings)
    client_id = register(client)
    location = authorize(client, client_id, redirect_uri=None).headers["location"]
    code = code_from(approve(client, headers, request_id_from(location)))
    resp = token_request(
        client,
        grant_type="authorization_code",
        code=code,
        client_id=client_id,
        code_verifier=VERIFIER,
        resource=RESOURCE,
    )
    assert resp.status_code == 200, resp.text


def test_a_backslash_redirect_uri_is_never_followed(client: TestClient) -> None:
    client_id = register(client, "http://localhost/callback")
    resp = authorize(client, client_id, redirect_uri="http://evil.com\\@localhost:1/callback")
    assert resp.status_code == 302
    assert "evil.com" not in resp.headers["location"]
    assert "/oauth/consent?error=" in resp.headers["location"]
