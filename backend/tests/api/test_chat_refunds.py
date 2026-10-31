"""A chat that fails before its turn starts gives its unit of the daily allowance back.

Before: the gate reserved a unit, then an error between the gate and the turn start (a bad
attachment, an unknown thread, an unknown 語氣, a failed save) kept it. Three such errors locked a
Free user (3 chats a day) out for the rest of the UTC day without one answer. The chat-quota model
(`.claude/skills/chat-quota-model`, findings F1 and F2) found these paths."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.routers import threads_actions
from config.settings import Settings
from conversation.store import SqlAlchemyConversationStore
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in
from tests.api.test_idempotent_turns import _chats_used
from tests.api.test_routes import _parse_sse

_FREE_CAP = 3


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")


def _query(client: TestClient, headers: dict[str, str], **body: Any):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/agent/query", json={"message": "hello", **body}, headers=headers)


def _answered(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    resp = _query(client, headers)
    assert resp.status_code == 200
    _parse_sse(resp.text)
    threads = client.get("/api/v1/threads", headers=headers).json()["threads"]
    thread_id = threads[0]["id"]
    return client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()


def _broken_save(*args: object, **kwargs: object) -> object:
    del args, kwargs
    raise RuntimeError("the database went away")


_REFUSED_SENDS = {
    "bad attachment": {
        "attachments": [{"filename": "a.txt", "media_type": "text/plain", "data_base64": "!!"}]
    },
    "unknown thread": {"thread_id": "no-such-thread"},
    "unknown personality": {"personality": "no-such-tone"},
}


@pytest.mark.parametrize("body", _REFUSED_SENDS.values(), ids=_REFUSED_SENDS.keys())
def test_a_send_refused_after_the_gate_costs_no_chat(
    settings: Settings, body: dict[str, Any]
) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        for _ in range(_FREE_CAP):
            assert _query(client, headers, **body).status_code in {404, 422}

        assert _chats_used(client, user_id) == 0
        assert _query(client, headers).status_code == 200  # not chat_limit_reached


def test_a_send_whose_save_fails_costs_no_chat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)
        monkeypatch.setattr(SqlAlchemyConversationStore, "append_turn", _broken_save)

        with pytest.raises(RuntimeError):
            _query(client, headers)

        assert _chats_used(client, user_id) == 0


def test_a_copy_that_slipped_past_the_duplicate_check_is_refunded_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two copies of one keyed message that both pass the check before the gate: the second one's
    save hits the unique key, it is told it is a duplicate, and its chat comes back once."""
    real_lookup = SqlAlchemyConversationStore.turn_for_idempotency_key
    lookups = 0

    async def first_lookup_misses(self: SqlAlchemyConversationStore, **kwargs: str):  # type: ignore[no-untyped-def]
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real_lookup(self, **kwargs)

    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)
        keyed = {**headers, "Idempotency-Key": "msg-1"}
        first = client.post("/api/v1/agent/query", json={"message": "hello"}, headers=keyed)
        _parse_sse(first.text)
        monkeypatch.setattr(
            SqlAlchemyConversationStore, "turn_for_idempotency_key", first_lookup_misses
        )

        again = client.post("/api/v1/agent/query", json={"message": "hello"}, headers=keyed)

        assert again.status_code == 409
        assert again.json()["code"] == "duplicate_request"
        assert _chats_used(client, user_id) == 1


def test_an_edit_whose_save_fails_costs_no_chat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)
        thread = _answered(client, headers)
        user_turn = next(t for t in thread["turns"] if t["role"] == "user")
        monkeypatch.setattr(SqlAlchemyConversationStore, "append_turn", _broken_save)

        with pytest.raises(RuntimeError):
            client.post(
                f"/api/v1/threads/{thread['id']}/turns/{user_turn['id']}/edit",
                json={"message": "edited"},
                headers=headers,
            )

        assert _chats_used(client, user_id) == 1  # only the answered send


def test_a_regenerate_that_cannot_start_costs_no_chat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)
        thread = _answered(client, headers)
        answer = next(t for t in thread["turns"] if t["role"] == "assistant")
        monkeypatch.setattr(threads_actions, "start_turn_stream", _broken_save)

        with pytest.raises(RuntimeError):
            client.post(
                f"/api/v1/threads/{thread['id']}/turns/{answer['id']}/regenerate",
                headers=headers,
            )

        assert _chats_used(client, user_id) == 1  # only the answered send


def test_a_started_turn_keeps_its_chat(settings: Settings) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        _answered(client, headers)

        assert _chats_used(client, user_id) == 1
