"""Telegram linking: a one-time link token, bound by the bot webhook, surfaced in channel status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from scheduler.store import SqlAlchemySchedulerStore
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

_SECRET = "hook-secret"
_HEADER = "X-Telegram-Bot-Api-Secret-Token"


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
        telegram_bot_token="bot-token",
        telegram_bot_username="@miru_bot",
        telegram_webhook_secret=_SECRET,
    )


@pytest.fixture
def linked_client(settings: Settings):
    with build_test_client(settings, agent_answer="x") as test_client:
        yield test_client


def _token_from(url: str) -> str:
    assert url.startswith("https://t.me/miru_bot?start=")
    return url.split("start=", 1)[1]


def _start(token: str) -> dict[str, object]:
    return {"message": {"text": f"/start {token}", "chat": {"id": 998877}}}


def test_the_whole_link_flow_binds_the_chat_and_single_uses_the_token(
    linked_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(linked_client, settings, tier=Tier.FREE)
    before = linked_client.get("/api/v1/me/channels", headers=headers).json()
    assert before["telegram"] is False
    assert before["telegram_available"] is True

    link = linked_client.post("/api/v1/me/channels/telegram/link", headers=headers).json()
    token = _token_from(link["url"])

    hook = linked_client.post(
        "/api/v1/webhooks/telegram", json=_start(token), headers={_HEADER: _SECRET}
    )
    assert hook.json() == {"linked": True}
    assert linked_client.get("/api/v1/me/channels", headers=headers).json()["telegram"] is True

    # The token was single-use: replaying it (another chat) binds nothing.
    replay = linked_client.post(
        "/api/v1/webhooks/telegram", json=_start(token), headers={_HEADER: _SECRET}
    )
    assert replay.json() == {"linked": False}

    assert linked_client.delete("/api/v1/me/channels/telegram", headers=headers).status_code == 204
    assert linked_client.get("/api/v1/me/channels", headers=headers).json()["telegram"] is False


def test_the_webhook_rejects_a_caller_without_the_secret(linked_client: TestClient) -> None:
    body = _start("whatever")
    assert linked_client.post("/api/v1/webhooks/telegram", json=body).status_code == 403
    bad = linked_client.post("/api/v1/webhooks/telegram", json=body, headers={_HEADER: "nope"})
    assert bad.status_code == 403


def test_an_expired_token_binds_nothing(linked_client: TestClient, settings: Settings) -> None:
    headers, user_id = sign_in(linked_client, settings, tier=Tier.FREE)
    state = linked_client.app.state.app_state
    assert linked_client.portal is not None

    async def _expired() -> None:
        async with state.session_factory() as session:
            await SqlAlchemySchedulerStore(session).start_channel_link(
                user_id=user_id,
                channel="telegram",
                token="old-token",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            await session.commit()

    linked_client.portal.call(_expired)
    hook = linked_client.post(
        "/api/v1/webhooks/telegram", json=_start("old-token"), headers={_HEADER: _SECRET}
    )
    assert hook.json() == {"linked": False}
    assert linked_client.get("/api/v1/me/channels", headers=headers).json()["telegram"] is False


def test_linking_is_unavailable_when_the_bot_is_not_configured(client: TestClient) -> None:
    # The shared `settings` fixture is overridden in this module, so build an unconfigured app.
    unconfigured = Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        telegram_bot_token=None,
        telegram_bot_username=None,
    )
    with build_test_client(unconfigured, agent_answer="x") as bare:
        headers, _ = sign_in(bare, unconfigured, tier=Tier.FREE)
        resp = bare.post("/api/v1/me/channels/telegram/link", headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "channel_unavailable"
    del client


def test_one_chat_cannot_be_bound_to_two_accounts(
    linked_client: TestClient, settings: Settings
) -> None:
    first, _ = sign_in(linked_client, settings, tier=Tier.FREE)
    second, _ = sign_in(linked_client, settings, tier=Tier.FREE)
    token_a = _token_from(
        linked_client.post("/api/v1/me/channels/telegram/link", headers=first).json()["url"]
    )
    token_b = _token_from(
        linked_client.post("/api/v1/me/channels/telegram/link", headers=second).json()["url"]
    )
    hook = {_HEADER: _SECRET}
    assert linked_client.post(
        "/api/v1/webhooks/telegram", json=_start(token_a), headers=hook
    ).json() == {"linked": True}
    # The same chat (998877) tries to claim a second account.
    assert linked_client.post(
        "/api/v1/webhooks/telegram", json=_start(token_b), headers=hook
    ).json() == {"linked": False}
    assert linked_client.get("/api/v1/me/channels", headers=second).json()["telegram"] is False
