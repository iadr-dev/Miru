"""End-to-end proof that `regenerate`, `edit`, and `feedback` are real — driven through the
actual API routes (`TestClient`, real store, real orchestrator with a stub `LLMClient`), matching
the setup pattern established throughout `tests/api/test_rich_card_route.py` and
`tests/api/test_routes.py`.

- `regenerate_turn`: a real second assistant turn is created as a sibling of the original, the
  original turn's own text is untouched, and the new turn carries the same non-trading caveat
  governance stamps on every turn (proving it went through the real guardrail path, not a
  special-cased shortcut).
- `edit_turn`: a new branch (new user turn + new assistant turn) is created, the original user
  turn's text is untouched, and `switch_branch`-style navigation can move between the old and new
  branches afterward.
- `turn_feedback`: a real DB row is written, and a re-submit for the same `(turn_id, user_id)`
  updates rather than duplicates.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from db.models import TurnFeedbackRow
from schemas.enums import Tier
from tests.api.conftest import observe_db, sign_in
from tests.api.test_routes import _parse_sse


def _feedback_row_count(client: TestClient, *, turn_id: str, user_id: str) -> int:
    session_factory = client.app.state.app_state.session_factory

    async def _count() -> int:
        async with session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(TurnFeedbackRow)
                .where(TurnFeedbackRow.turn_id == turn_id, TurnFeedbackRow.user_id == user_id)
            )
            return result.scalar_one()

    return observe_db(client, _count)


def _create_thread(client: TestClient, headers: dict[str, str], title: str = "t") -> str:
    resp = client.post("/api/v1/threads", json={"title": title}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def _run_query(client: TestClient, headers: dict[str, str], message: str) -> None:
    resp = client.post("/api/v1/agent/query", json={"message": message}, headers=headers)
    assert resp.status_code == 200
    _parse_sse(resp.text)  # drain so the background driver finishes persisting


def _get_thread(client: TestClient, headers: dict[str, str], thread_id: str) -> dict[str, object]:
    resp = client.get(f"/api/v1/threads/{thread_id}", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _only_thread_id(client: TestClient, headers: dict[str, str]) -> str:
    threads = client.get("/api/v1/threads", headers=headers).json()["threads"]
    return str(threads[0]["id"])


def _assistant_turn(full_thread: dict[str, object]) -> dict[str, object]:
    turns = full_thread["turns"]
    assert isinstance(turns, list)
    return next(t for t in turns if t["role"] == "assistant")


def _user_turn(full_thread: dict[str, object]) -> dict[str, object]:
    turns = full_thread["turns"]
    assert isinstance(turns, list)
    return next(t for t in turns if t["role"] == "user")


def _text_of(turn: dict[str, object]) -> str:
    for message in turn["messages"]:
        if message["type"] == "text":
            return str(message["content"]["text"])
    raise AssertionError("turn has no text block")


def _text_block_parent_id(turn: dict[str, object]) -> str:
    for message in turn["messages"]:
        if message["type"] == "text":
            return str(message["parent_id"])
    raise AssertionError("turn has no text block")


class TestRegenerateTurn:
    def test_regenerate_creates_a_sibling_assistant_turn_and_preserves_the_original(
        self, client: TestClient, signed_in_user, agent_answer: str
    ) -> None:
        headers, _ = signed_in_user
        _run_query(client, headers, "台積電最近如何")
        thread_id = _only_thread_id(client, headers)
        full = _get_thread(client, headers, thread_id)
        original_assistant = _assistant_turn(full)
        original_text = _text_of(original_assistant)
        assert agent_answer in original_text

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{original_assistant['id']}/regenerate",
            headers=headers,
        )
        assert resp.status_code == 200
        frames = _parse_sse(resp.text)
        kinds = [f["event"] for f in frames]
        assert kinds[0] == "turn.start"
        assert kinds[-1] == "turn.end"
        assert "message" in kinds

        # A real second assistant turn now exists — the original is untouched (immutability).
        full_after = _get_thread(client, headers, thread_id)
        assistant_turns = [t for t in full_after["turns"] if t["role"] == "assistant"]
        assert len(assistant_turns) == 2
        original_after = next(t for t in assistant_turns if t["id"] == original_assistant["id"])
        assert _text_of(original_after) == original_text

        new_turn = next(t for t in assistant_turns if t["id"] != original_assistant["id"])
        assert new_turn["regenerate_of"] == original_assistant["id"]
        # Same real guardrail path as any other turn: the answer already carries its own Chinese
        # non-trading caveat, so no second (English) one is stacked underneath it.
        assert "非投資建議" in _text_of(new_turn)
        assert "not a buy/sell/hold recommendation" not in _text_of(new_turn)

        # Both assistant turns are siblings under the same user turn's last message.
        user_turn = _user_turn(full_after)
        assert _text_block_parent_id(original_after) == _text_block_parent_id(new_turn)
        assert _text_block_parent_id(original_after) == user_turn["messages"][-1]["id"]

    def test_regenerate_on_a_user_turn_is_rejected(
        self, client: TestClient, signed_in_user
    ) -> None:
        headers, _ = signed_in_user
        _run_query(client, headers, "hi")
        thread_id = _only_thread_id(client, headers)
        full = _get_thread(client, headers, thread_id)
        user_turn = _user_turn(full)

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{user_turn['id']}/regenerate", headers=headers
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "not_an_assistant_turn"

    def test_regenerate_unknown_turn_404(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.post(f"/api/v1/threads/{thread_id}/turns/missing/regenerate", headers=headers)
        assert resp.status_code == 404


class TestEditTurn:
    def test_edit_creates_a_new_branch_and_preserves_the_original_user_text(
        self, client: TestClient, signed_in_user
    ) -> None:
        headers, _ = signed_in_user
        _run_query(client, headers, "original question")
        thread_id = _only_thread_id(client, headers)
        full = _get_thread(client, headers, thread_id)
        original_user_turn = _user_turn(full)
        assert _text_of(original_user_turn) == "original question"

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{original_user_turn['id']}/edit",
            json={"message": "edited question"},
            headers=headers,
        )
        assert resp.status_code == 200
        frames = _parse_sse(resp.text)
        kinds = [f["event"] for f in frames]
        assert kinds[0] == "turn.start"
        assert kinds[-1] == "turn.end"

        full_after = _get_thread(client, headers, thread_id)
        user_turns = [t for t in full_after["turns"] if t["role"] == "user"]
        assistant_turns = [t for t in full_after["turns"] if t["role"] == "assistant"]
        assert len(user_turns) == 2
        assert len(assistant_turns) == 2

        # The original user turn's text is untouched — canonical immutability.
        original_after = next(t for t in user_turns if t["id"] == original_user_turn["id"])
        assert _text_of(original_after) == "original question"

        new_user_turn = next(t for t in user_turns if t["id"] != original_user_turn["id"])
        assert _text_of(new_user_turn) == "edited question"

        # The new user turn is a sibling of the original (same parent message).
        assert _text_block_parent_id(original_after) == _text_block_parent_id(new_user_turn)

        # switch_branch can navigate between the old and new branches afterward: the assistant
        # turn whose text block's parent is the original user turn's own message is the "old"
        # branch's leaf; the other assistant turn is the "new" branch's leaf.
        original_user_message_id = next(
            m["id"] for m in original_after["messages"] if m["type"] == "text"
        )
        old_assistant = next(
            t for t in assistant_turns if _text_block_parent_id(t) == original_user_message_id
        )
        new_assistant = next(t for t in assistant_turns if t["id"] != old_assistant["id"])

        switched = client.post(
            f"/api/v1/threads/{thread_id}/switch-branch",
            json={"target_message_id": old_assistant["messages"][-1]["id"]},
            headers=headers,
        )
        assert switched.status_code == 200
        assert switched.json()["active_leaf_message_id"] == old_assistant["messages"][-1]["id"]

        switched_back = client.post(
            f"/api/v1/threads/{thread_id}/switch-branch",
            json={"target_message_id": new_assistant["messages"][-1]["id"]},
            headers=headers,
        )
        assert switched_back.status_code == 200
        assert switched_back.json()["active_leaf_message_id"] == new_assistant["messages"][-1]["id"]

    def test_edit_on_an_assistant_turn_is_rejected(
        self, client: TestClient, signed_in_user
    ) -> None:
        headers, _ = signed_in_user
        _run_query(client, headers, "hi")
        thread_id = _only_thread_id(client, headers)
        full = _get_thread(client, headers, thread_id)
        assistant_turn = _assistant_turn(full)

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{assistant_turn['id']}/edit",
            json={"message": "nope"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "not_a_user_turn"


class TestTurnFeedback:
    def test_submit_persists_a_real_row(self, client: TestClient, signed_in_user) -> None:
        headers, user_id = signed_in_user
        _run_query(client, headers, "hi")
        thread_id = _only_thread_id(client, headers)
        turn = _assistant_turn(_get_thread(client, headers, thread_id))

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{turn['id']}/feedback",
            json={"rating": "up", "comment": "great"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rating"] == "up"
        assert body["comment"] == "great"
        assert _feedback_row_count(client, turn_id=turn["id"], user_id=user_id) == 1

    def test_resubmit_updates_rather_than_duplicates(
        self, client: TestClient, signed_in_user
    ) -> None:
        headers, user_id = signed_in_user
        _run_query(client, headers, "hi")
        thread_id = _only_thread_id(client, headers)
        turn = _assistant_turn(_get_thread(client, headers, thread_id))

        first = client.post(
            f"/api/v1/threads/{thread_id}/turns/{turn['id']}/feedback",
            json={"rating": "down", "comment": "meh"},
            headers=headers,
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/threads/{thread_id}/turns/{turn['id']}/feedback",
            json={"rating": "up", "comment": None},
            headers=headers,
        )
        assert second.status_code == 200
        assert second.json()["rating"] == "up"
        assert second.json()["comment"] is None
        assert _feedback_row_count(client, turn_id=turn["id"], user_id=user_id) == 1

    def test_feedback_requires_valid_rating(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        _run_query(client, headers, "hi")
        thread_id = _only_thread_id(client, headers)
        turn = _assistant_turn(_get_thread(client, headers, thread_id))

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{turn['id']}/feedback",
            json={"rating": "sideways"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestTurnActionsIsolation:
    def test_user_b_cannot_regenerate_user_a_turn(
        self, client: TestClient, settings, signed_in_user
    ) -> None:
        headers_a, _ = signed_in_user
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        _run_query(client, headers_a, "hi")
        thread_id = _only_thread_id(client, headers_a)
        turn = _assistant_turn(_get_thread(client, headers_a, thread_id))

        resp = client.post(
            f"/api/v1/threads/{thread_id}/turns/{turn['id']}/regenerate", headers=headers_b
        )
        assert resp.status_code == 404
