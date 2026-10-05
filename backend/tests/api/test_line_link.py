"""LINE linking: a one-time code sent to the official account, bound by the signed webhook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api.routers.scheduler import get_line_transport
from config.settings import Settings
from scheduler.delivery.transport import TransportResult
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in

_SECRET = "line-channel-secret"
_WEBHOOK = "/api/v1/channels/line/webhook"


class _FakeTransport:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult:
        del url, headers
        messages = body["messages"]
        self.replies.append(str(messages[0]["text"]))  # type: ignore[index]
        return TransportResult(ok=True, status=200)


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
        line_messaging_channel_access_token="line-token",
        line_messaging_channel_secret=_SECRET,
        line_bot_basic_id="@abc123",
    )


@pytest.fixture
def line_client(settings: Settings):
    transport = _FakeTransport()
    with build_test_client(settings, agent_answer="x") as client:
        client.app.dependency_overrides[get_line_transport] = lambda: transport
        client.transport = transport  # type: ignore[attr-defined]
        yield client


def _signed(events: list[dict[str, object]], secret: str = _SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps({"events": events}).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return body, {"X-Line-Signature": signature, "Content-Type": "application/json"}


def _message(text: str, user: str = "U123") -> dict[str, object]:
    return {
        "type": "message",
        "replyToken": "rt",
        "source": {"type": "user", "userId": user},
        "message": {"type": "text", "text": text},
    }


def test_the_whole_flow_binds_the_senders_line_id_and_replies(
    line_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(line_client, settings, tier=Tier.FREE)
    before = line_client.get("/api/v1/me/channels", headers=headers).json()
    assert before["line_available"] is True
    assert before["line_linked"] is False

    link = line_client.post("/api/v1/me/channels/line/link", headers=headers).json()
    assert link["add_friend_url"] == "https://line.me/R/ti/p/@abc123"
    code = link["code"]
    assert len(code) == 8

    body, sig = _signed([_message(f"連結 {code.lower()}")])  # case and a prefix do not matter
    assert line_client.post(_WEBHOOK, content=body, headers=sig).json() == {"handled": 1}
    assert line_client.transport.replies == ["已連結完成，之後的提醒會傳到這裡。"]  # type: ignore[attr-defined]
    after = line_client.get("/api/v1/me/channels", headers=headers).json()
    assert after["line_linked"] is True
    assert after["line"] is True

    # The code was single-use: a second person sending it binds nothing.
    body, sig = _signed([_message(code, user="U999")])
    line_client.post(_WEBHOOK, content=body, headers=sig)
    assert "連結 LINE" in line_client.transport.replies[-1]  # type: ignore[attr-defined]

    assert line_client.delete("/api/v1/me/channels/line", headers=headers).status_code == 204
    assert line_client.get("/api/v1/me/channels", headers=headers).json()["line_linked"] is False


def test_a_request_without_a_valid_signature_is_refused(line_client: TestClient) -> None:
    body, _ = _signed([_message("ABCDEFGH")])
    assert line_client.post(_WEBHOOK, content=body).status_code == 403
    bad = {"X-Line-Signature": "AAAA", "Content-Type": "application/json"}
    assert line_client.post(_WEBHOOK, content=body, headers=bad).status_code == 403
    other_secret_body, other_sig = _signed([_message("ABCDEFGH")], secret="not-the-secret")
    assert (
        line_client.post(_WEBHOOK, content=other_secret_body, headers=other_sig).status_code == 403
    )


def test_the_consoles_verify_button_an_empty_event_list_is_accepted(
    line_client: TestClient,
) -> None:
    body, sig = _signed([])
    assert line_client.post(_WEBHOOK, content=body, headers=sig).status_code == 200


def test_follow_gets_instructions_and_unfollow_drops_the_link(
    line_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(line_client, settings, tier=Tier.FREE)
    code = line_client.post("/api/v1/me/channels/line/link", headers=headers).json()["code"]
    body, sig = _signed([_message(code, user="U777")])
    line_client.post(_WEBHOOK, content=body, headers=sig)
    assert line_client.get("/api/v1/me/channels", headers=headers).json()["line_linked"] is True

    follow = {"type": "follow", "replyToken": "rt2", "source": {"type": "user", "userId": "U555"}}
    body, sig = _signed([follow])
    line_client.post(_WEBHOOK, content=body, headers=sig)
    assert "連結 LINE" in line_client.transport.replies[-1]  # type: ignore[attr-defined]

    unfollow = {"type": "unfollow", "source": {"type": "user", "userId": "U777"}}
    body, sig = _signed([unfollow])
    line_client.post(_WEBHOOK, content=body, headers=sig)
    assert line_client.get("/api/v1/me/channels", headers=headers).json()["line_linked"] is False


def test_linking_is_unavailable_without_the_bot_credentials() -> None:
    bare = Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        line_messaging_channel_access_token=None,
        line_messaging_channel_secret=None,
    )
    with build_test_client(bare, agent_answer="x") as client:
        headers, _ = sign_in(client, bare, tier=Tier.FREE)
        resp = client.post("/api/v1/me/channels/line/link", headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "channel_unavailable"
