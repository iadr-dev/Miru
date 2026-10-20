"""Characterization of the LINE webhook's event loop: which events count as handled, which are
skipped, and when a reply is sent."""

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
_HELP_MARKER = "連結 LINE"
_LINKED = "已連結完成，之後的提醒會傳到這裡。"


class _FakeTransport:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []

    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult:
        del url, headers
        messages = body["messages"]
        self.replies.append((str(body["replyToken"]), str(messages[0]["text"])))  # type: ignore[index]
        return TransportResult(ok=True, status=200)


def _settings(*, token: str | None = "line-token") -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        line_messaging_channel_access_token=token,
        line_messaging_channel_secret=_SECRET,
        line_bot_basic_id="@abc123",
    )


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def line_client(settings: Settings):
    transport = _FakeTransport()
    with build_test_client(settings, agent_answer="x") as client:
        client.app.dependency_overrides[get_line_transport] = lambda: transport
        client.transport = transport  # type: ignore[attr-defined]
        yield client


def _sign(body: bytes) -> dict[str, str]:
    sig = base64.b64encode(hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return {"X-Line-Signature": sig, "Content-Type": "application/json"}


def _post(client: TestClient, payload: object) -> dict[str, int]:
    body = json.dumps(payload).encode()
    resp = client.post(_WEBHOOK, content=body, headers=_sign(body))
    assert resp.status_code == 200
    return resp.json()


def _replies(client: TestClient) -> list[tuple[str, str]]:
    return client.transport.replies  # type: ignore[attr-defined, no-any-return]


def _event(kind: str, user: object = "U1", **extra: object) -> dict[str, object]:
    event: dict[str, object] = {"type": kind, "source": {"type": "user", "userId": user}}
    event.update(extra)
    return event


def test_unparseable_or_empty_bodies_handle_nothing(line_client: TestClient) -> None:
    for raw in (b"not json", b"\xff\xfe", b""):
        resp = line_client.post(_WEBHOOK, content=raw, headers=_sign(raw))
        assert resp.status_code == 200
        assert resp.json() == {"handled": 0}


def test_events_that_are_missing_or_not_a_list_handle_nothing(line_client: TestClient) -> None:
    assert _post(line_client, {}) == {"handled": 0}
    assert _post(line_client, {"events": {"type": "follow"}}) == {"handled": 0}
    assert _post(line_client, {"events": "follow"}) == {"handled": 0}
    assert _replies(line_client) == []


def test_malformed_events_are_skipped_without_counting(line_client: TestClient) -> None:
    events: list[object] = [
        "not-a-dict",
        42,
        {"type": "follow", "replyToken": "r"},  # no source
        {"type": "follow", "replyToken": "r", "source": "U1"},  # source not a dict
        {"type": "follow", "replyToken": "r", "source": {"type": "group"}},  # no userId
        {"type": "follow", "replyToken": "r", "source": {"userId": 7}},  # userId not a string
    ]
    assert _post(line_client, {"events": events}) == {"handled": 0}
    assert _replies(line_client) == []


def test_unknown_event_kinds_are_not_handled(line_client: TestClient) -> None:
    events = [_event("postback", replyToken="r"), _event("join", replyToken="r"), _event("zzz")]
    assert _post(line_client, {"events": events}) == {"handled": 0}
    assert _replies(line_client) == []


def test_follow_and_codeless_messages_reply_with_the_instructions(line_client: TestClient) -> None:
    events = [
        _event("follow", replyToken="r-follow"),
        _event("message", replyToken="r-text", message={"type": "text", "text": "hello"}),
        _event("message", replyToken="r-sticker", message={"type": "sticker"}),
        _event("message", replyToken="r-nomsg"),
        _event("message", replyToken="r-badmsg", message="oops"),
        _event("message", replyToken="r-badtext", message={"type": "text", "text": 5}),
    ]
    assert _post(line_client, {"events": events}) == {"handled": 6}
    replies = _replies(line_client)
    assert [token for token, _ in replies] == [
        "r-follow",
        "r-text",
        "r-sticker",
        "r-nomsg",
        "r-badmsg",
        "r-badtext",
    ]
    assert all(_HELP_MARKER in text for _, text in replies)


def test_an_unknown_code_replies_with_the_instructions_not_the_confirmation(
    line_client: TestClient,
) -> None:
    event = _event("message", replyToken="r", message={"type": "text", "text": "ZZZZZZZZ"})
    assert _post(line_client, {"events": [event]}) == {"handled": 1}
    [(token, text)] = _replies(line_client)
    assert token == "r"
    assert _HELP_MARKER in text
    assert text != _LINKED


def test_an_event_without_a_string_reply_token_is_handled_but_not_answered(
    line_client: TestClient,
) -> None:
    events = [
        _event("follow"),
        _event("follow", replyToken=None),
        _event("message", replyToken=123, message={"type": "text", "text": "hi"}),
    ]
    assert _post(line_client, {"events": events}) == {"handled": 3}
    assert _replies(line_client) == []


def test_unfollow_is_counted_and_never_replied_to(line_client: TestClient) -> None:
    events = [_event("unfollow", replyToken="r"), _event("unfollow")]
    assert _post(line_client, {"events": events}) == {"handled": 2}
    assert _replies(line_client) == []


def test_a_mixed_batch_counts_only_handled_events_and_keeps_going(
    line_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(line_client, settings, tier=Tier.FREE)
    code = line_client.post("/api/v1/me/channels/line/link", headers=headers).json()["code"]
    events: list[object] = [
        _event("unfollow", user="U-other"),
        "junk",
        _event("message", user="U-new", replyToken="r1", message={"type": "text", "text": code}),
        _event("postback"),
        _event("follow", user="U-f", replyToken="r2"),
    ]
    assert _post(line_client, {"events": events}) == {"handled": 3}
    assert _replies(line_client) == [("r1", _LINKED), ("r2", _replies(line_client)[1][1])]
    assert _HELP_MARKER in _replies(line_client)[1][1]
    assert line_client.get("/api/v1/me/channels", headers=headers).json()["line_linked"] is True


def test_without_an_access_token_events_are_handled_but_nothing_is_sent() -> None:
    bare = _settings(token=None)
    transport = _FakeTransport()
    with build_test_client(bare, agent_answer="x") as client:
        client.app.dependency_overrides[get_line_transport] = lambda: transport
        events = [_event("follow", replyToken="r"), _event("unfollow")]
        assert _post(client, {"events": events}) == {"handled": 2}
    assert transport.replies == []
