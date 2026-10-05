"""The unsubscribe endpoints: a GET only asks, a POST does it, and a bad token does nothing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.unsubscribe import make_token
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

_URL = "/api/v1/email/unsubscribe"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        resend_api_key="re_test",
        email_from_address="Miru <alerts@miru.test>",
    )


@pytest.fixture
def client(settings: Settings):
    with build_test_client(settings, agent_answer="x") as test_client:
        yield test_client


def _user_email(client: TestClient, user_id: str) -> str:
    state = client.app.state.app_state
    assert client.portal is not None

    async def _get() -> str:
        from accounts.store import SqlAlchemyUserStore  # noqa: PLC0415 — test-local

        async with state.session_factory() as session:
            user = await SqlAlchemyUserStore(session).get(user_id=user_id)
            assert user.email
            return user.email

    return client.portal.call(_get)


def _suppressed(client: TestClient, address: str) -> bool:
    state = client.app.state.app_state
    assert client.portal is not None

    async def _check() -> bool:
        async with state.session_factory() as session:
            return await SqlAlchemySchedulerStore(session).is_email_suppressed(address=address)

    return client.portal.call(_check)


def test_a_get_asks_for_confirmation_and_changes_nothing(
    client: TestClient, settings: Settings
) -> None:
    _, user_id = sign_in(client, settings, tier=Tier.FREE)
    email = _user_email(client, user_id)
    token = make_token(_TEST_JWT_SECRET, user_id, email)
    page = client.get(_URL, params={"token": token})
    assert page.status_code == 200
    assert "確認取消" in page.text
    assert not _suppressed(client, email)  # a mail scanner opening the link unsubscribes nobody


def test_a_post_unsubscribes_and_ticking_email_again_undoes_it(
    client: TestClient, settings: Settings
) -> None:
    headers, user_id = sign_in(client, settings, tier=Tier.FREE)
    email = _user_email(client, user_id)
    token = make_token(_TEST_JWT_SECRET, user_id, email)
    done = client.post(_URL, params={"token": token}, content="List-Unsubscribe=One-Click")
    assert done.status_code == 200
    assert _suppressed(client, email)

    saved = client.put(
        "/api/v1/me/alert-preferences", json={"channels": ["in_app", "email"]}, headers=headers
    )
    assert saved.status_code == 200
    assert not _suppressed(client, email)  # the user asked for email again


def test_a_forged_or_foreign_token_does_nothing(client: TestClient, settings: Settings) -> None:
    _, user_id = sign_in(client, settings, tier=Tier.FREE)
    email = _user_email(client, user_id)
    for token in (
        "garbage",
        f"{user_id}.deadbeef",
        make_token("another-secret", user_id, email),
        make_token(_TEST_JWT_SECRET, "someone-else", email),
    ):
        assert client.post(_URL, params={"token": token}).status_code == 400
        assert client.get(_URL, params={"token": token}).status_code == 400
    assert not _suppressed(client, email)


def test_the_settings_status_shows_why_an_address_is_not_emailed(
    client: TestClient, settings: Settings
) -> None:
    headers, user_id = sign_in(client, settings, tier=Tier.FREE)
    email = _user_email(client, user_id)
    status = client.get("/api/v1/me/channels", headers=headers).json()
    assert status["email"] is True
    assert status["email_blocked"] is None

    token = make_token(_TEST_JWT_SECRET, user_id, email)
    client.post(_URL, params={"token": token})
    status = client.get("/api/v1/me/channels", headers=headers).json()
    assert status["email"] is False
    assert status["email_blocked"] == "unsubscribed"


def test_a_placeholder_from_address_is_not_a_ready_email_channel() -> None:
    placeholder = Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        resend_api_key="re_test",
        email_from_address="alerts@your-domain.example",
    )
    with build_test_client(placeholder, agent_answer="x") as bare:
        headers, _ = sign_in(bare, placeholder, tier=Tier.FREE)
        assert bare.get("/api/v1/me/channels", headers=headers).json()["email"] is False


def test_resubscribe_undoes_an_unsubscribe_but_never_a_bounce(
    client: TestClient, settings: Settings
) -> None:
    _, user_id = sign_in(client, settings, tier=Tier.FREE)
    email = _user_email(client, user_id)
    token = make_token(_TEST_JWT_SECRET, user_id, email)
    client.post(_URL, params={"token": token})
    # the link now offers the opposite action
    assert "重新訂閱" in client.get(_URL, params={"token": token}).text

    assert client.post("/api/v1/email/resubscribe", params={"token": token}).status_code == 200
    assert not _suppressed(client, email)

    _suppress(client, email, "bounced")
    client.post("/api/v1/email/resubscribe", params={"token": token})
    assert _suppressed(client, email)  # a bounce is not the user's to undo
    assert client.post("/api/v1/email/resubscribe", params={"token": "bad"}).status_code == 400


def _suppress(client: TestClient, address: str, reason: str) -> None:
    state = client.app.state.app_state
    assert client.portal is not None

    async def _do() -> None:
        async with state.session_factory() as session:
            await SqlAlchemySchedulerStore(session).suppress_email(address=address, reason=reason)
            await session.commit()

    client.portal.call(_do)
