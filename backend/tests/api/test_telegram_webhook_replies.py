"""The Telegram webhook answers the chat: a confirmation once linked, help otherwise, and always a
200 so Telegram never retries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.routers.scheduler import get_telegram_transport
from config.settings import Settings
from scheduler.delivery.transport import TransportResult
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

_SECRET = "hook-secret"
_HEADER = "X-Telegram-Bot-Api-Secret-Token"
_WEBHOOK = "/api/v1/webhooks/telegram"
_CHAT = 998877
_HELP_MARKER = "連結 Telegram"
_LINKED = "已連結完成，之後的提醒會傳到這裡。"


class _FakeTransport:
    def __init__(self, result: TransportResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._result = result or TransportResult(ok=True, status=200)

    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult:
        del headers
        self.calls.append((url, body))
        return self._result


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
def tg_client(settings: Settings):
    transport = _FakeTransport()
    with build_test_client(settings, agent_answer="x") as client:
        client.app.dependency_overrides[get_telegram_transport] = lambda: transport
        client.transport = transport  # type: ignore[attr-defined]
        yield client


def _transport(client: TestClient) -> _FakeTransport:
    return client.transport  # type: ignore[attr-defined, no-any-return]


def _say(client: TestClient, text: str) -> dict[str, bool]:
    update = {"message": {"text": text, "chat": {"id": _CHAT}}}
    resp = client.post(_WEBHOOK, json=update, headers={_HEADER: _SECRET})
    assert resp.status_code == 200
    return resp.json()


def _issue_token(client: TestClient, settings: Settings) -> str:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    url = client.post("/api/v1/me/channels/telegram/link", headers=headers).json()["url"]
    return url.split("start=", 1)[1]


def test_an_unknown_code_gets_the_help_text(tg_client: TestClient) -> None:
    assert _say(tg_client, "/start not-a-real-code") == {"linked": False}
    ((url, body),) = _transport(tg_client).calls
    assert url == "https://api.telegram.org/botbot-token/sendMessage"
    assert body["chat_id"] == str(_CHAT)
    assert _HELP_MARKER in str(body["text"])
    assert "parse_mode" not in body


@pytest.mark.parametrize("text", ["/start", "/start ", "/start@miru_bot"])
def test_a_bare_start_gets_the_help_text(tg_client: TestClient, text: str) -> None:
    assert _say(tg_client, text) == {"linked": False}
    ((_, body),) = _transport(tg_client).calls
    assert _HELP_MARKER in str(body["text"])


def test_a_successful_link_is_confirmed_in_the_chat(
    tg_client: TestClient, settings: Settings
) -> None:
    token = _issue_token(tg_client, settings)
    assert _say(tg_client, f"/start {token}") == {"linked": True}
    ((_, body),) = _transport(tg_client).calls
    assert body["text"] == _LINKED
    assert body["chat_id"] == str(_CHAT)


def test_other_chatter_is_not_answered(tg_client: TestClient) -> None:
    assert _say(tg_client, "hello") == {"linked": False}
    assert _transport(tg_client).calls == []


def test_a_failed_reply_never_turns_the_webhook_into_an_error(
    tg_client: TestClient, settings: Settings
) -> None:
    tg_client.app.dependency_overrides[get_telegram_transport] = lambda: _FakeTransport(
        TransportResult(ok=False, status=403, detail="http 403")
    )
    token = _issue_token(tg_client, settings)
    assert _say(tg_client, f"/start {token}") == {"linked": True}
