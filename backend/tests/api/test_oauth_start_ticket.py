"""Linking a provider whose redirect URI is on another origin (Telegram needs HTTPS, so dev goes
through a tunnel): the flow is started on that origin via a short-lived signed ticket, so the CSRF
cookies are set where the provider will send the browser back."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

_TUNNEL = "https://tunnel.example"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        google_client_id="g",
        google_client_secret="gs",
        google_redirect_uri="http://testserver/api/v1/auth/oauth/google/callback",
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        webhook_base_url=_TUNNEL,
        telegram_bot_token="8830287122:fake",
        telegram_login_client_secret="tg-secret",
        telegram_login_redirect_uri=f"{_TUNNEL}/api/v1/auth/oauth/telegram/callback",
    )


@pytest.fixture
def client(settings: Settings):
    with build_test_client(settings, agent_answer="x") as test_client:
        yield test_client


def _begin(client: TestClient, settings: Settings, provider: str) -> str:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    done = client.post(f"/api/v1/auth/oauth/{provider}/link", headers=headers)
    assert done.status_code == 200
    return done.json()["authorization_url"]


def test_a_tunnelled_provider_starts_on_the_tunnel_origin(
    client: TestClient, settings: Settings
) -> None:
    url = _begin(client, settings, "telegram")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == _TUNNEL
    assert parts.path == "/api/v1/auth/oauth/telegram/start"
    assert "ticket" in parse_qs(parts.query)


def test_a_same_origin_provider_keeps_the_direct_flow(
    client: TestClient, settings: Settings
) -> None:
    assert _begin(client, settings, "google").startswith("https://accounts.google.com/")


def test_redeeming_the_ticket_sets_the_flow_cookies_and_redirects_to_telegram(
    client: TestClient, settings: Settings
) -> None:
    ticket = parse_qs(urlsplit(_begin(client, settings, "telegram")).query)["ticket"][0]
    start = client.get(
        "/api/v1/auth/oauth/telegram/start", params={"ticket": ticket}, follow_redirects=False
    )
    assert start.status_code == 302
    location = start.headers["location"]
    assert location.startswith("https://oauth.telegram.org/auth?")
    assert parse_qs(urlsplit(location).query)["state"] == [client.cookies["oauth_state"]]
    assert client.cookies["oauth_link_user"]
    assert client.cookies["oauth_code_verifier"]


def test_a_forged_or_cross_provider_ticket_goes_to_the_error_screen(
    client: TestClient, settings: Settings
) -> None:
    ticket = parse_qs(urlsplit(_begin(client, settings, "telegram")).query)["ticket"][0]
    for path, token in (
        ("telegram", "x" * 40),
        ("telegram", ticket[:-3] + "abc"),
        ("google", ticket),  # a Telegram ticket cannot start a Google flow
    ):
        res = client.get(
            f"/api/v1/auth/oauth/{path}/start", params={"ticket": token}, follow_redirects=False
        )
        assert res.status_code == 302
        assert "/auth/error" in res.headers["location"]


def test_a_ticket_is_single_use(client: TestClient, settings: Settings) -> None:
    ticket = parse_qs(urlsplit(_begin(client, settings, "telegram")).query)["ticket"][0]
    path = "/api/v1/auth/oauth/telegram/start"
    first = client.get(path, params={"ticket": ticket}, follow_redirects=False)
    assert first.headers["location"].startswith("https://oauth.telegram.org/")
    again = client.get(path, params={"ticket": ticket}, follow_redirects=False)
    assert "/auth/error" in again.headers["location"]  # a replayed start URL does nothing


def test_a_ticket_is_not_a_session_and_a_session_is_not_a_ticket(
    client: TestClient, settings: Settings
) -> None:
    ticket = parse_qs(urlsplit(_begin(client, settings, "telegram")).query)["ticket"][0]
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401
    )
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    access = headers["Authorization"].removeprefix("Bearer ")
    res = client.get(
        "/api/v1/auth/oauth/telegram/start", params={"ticket": access}, follow_redirects=False
    )
    assert "/auth/error" in res.headers["location"]
