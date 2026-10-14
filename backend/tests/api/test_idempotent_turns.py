"""Sending the same message twice starts one turn and costs one chat.

Before: the `Idempotency-Key` header was recorded under a unique constraint and nothing else. A
repeat hit the constraint, which nobody caught (a 500), after the daily chat allowance had already
been charged for it. A retry after a dropped mobile connection, or a double-click, is exactly when a
client repeats a key."""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import func, select

from config.settings import Settings
from db import rls
from db.models.conversation import TurnRow
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in
from tests.api.sse_harness import collect_sse


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")


def _post(client, headers, key: str | None, message: str = "hello"):  # type: ignore[no-untyped-def]
    all_headers = {**headers, **({"Idempotency-Key": key} if key is not None else {})}
    return client.post("/api/v1/agent/query", json={"message": message}, headers=all_headers)


def _chats_used(client, user_id: str) -> int:  # type: ignore[no-untyped-def]
    state = client.app.state.app_state
    assert client.portal is not None

    async def count() -> int:
        with rls.system_access():
            return (await state.usage_store.today_counts(user_id=user_id)).get("chats", 0)

    return client.portal.call(count)


def _user_turns(client, user_id: str) -> int:  # type: ignore[no-untyped-def]
    state = client.app.state.app_state
    assert client.portal is not None

    async def count() -> int:
        with rls.system_access():
            async with state.session_factory() as session:
                return int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(TurnRow)
                            .where(TurnRow.user_id == user_id, TurnRow.role == "user")
                        )
                    ).scalar_one()
                )

    return client.portal.call(count)


def test_a_repeated_key_is_answered_with_where_the_first_turn_is_and_costs_nothing(
    settings: Settings,
) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        first = _post(client, headers, "msg-1")
        again = _post(client, headers, "msg-1")

        assert first.status_code == 200
        assert again.status_code == 409
        body = again.json()
        assert body["code"] == "duplicate_request"
        assert body["retryable"] is False
        assert again.headers["X-Thread-Id"]
        assert again.headers["X-Turn-Id"]
        assert _user_turns(client, user_id) == 1  # one turn, not two
        assert _chats_used(client, user_id) == 1  # and one chat taken from the allowance


def test_without_a_key_two_identical_messages_are_two_turns(settings: Settings) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        assert _post(client, headers, None).status_code == 200
        assert _post(client, headers, None).status_code == 200

        assert _user_turns(client, user_id) == 2


def test_the_same_key_from_two_users_is_not_a_duplicate(settings: Settings) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        a_headers, _ = sign_in(client, settings, tier=Tier.FREE)
        b_headers, _ = sign_in(client, settings, tier=Tier.FREE)

        assert _post(client, a_headers, "shared-key").status_code == 200
        assert _post(client, b_headers, "shared-key").status_code == 200


@pytest.mark.parametrize("key", ["x" * 129, "   ", "bad\nkey"])
def test_an_unusable_key_is_a_validation_error(settings: Settings, key: str) -> None:
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        response = _post(client, headers, key)

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"
        assert _chats_used(client, user_id) == 0


# ---- two copies arriving together (real Postgres) -----------------------------------------------


def _send_copies(client, headers, copies: int, key: str) -> list[str]:  # type: ignore[no-untyped-def]
    assert client.portal is not None

    async def send_all() -> list[list[str]]:
        return await asyncio.gather(
            *(
                collect_sse(
                    client.app,
                    "/api/v1/agent/query",
                    method="POST",
                    json_body={"message": "hello"},
                    until=lambda lines: "turn.end" in "".join(lines) or '"code"' in "".join(lines),
                    timeout_s=10,
                    headers={**headers, "Idempotency-Key": key},
                )
                for _ in range(copies)
            )
        )

    return ["".join(r) for r in client.portal.call(send_all)]


@pytest.mark.concurrency
def test_copies_sent_together_within_the_allowance_start_one_turn_and_charge_one_chat(
    settings: Settings,
) -> None:
    if not os.environ.get("RLS_TEST_POSTGRES_URL"):
        pytest.skip("RLS_TEST_POSTGRES_URL not set - Postgres race check skipped")
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        results = _send_copies(client, headers, 3, "race-1")  # the free allowance is 3 a day

        assert sum("turn.end" in r for r in results) == 1
        assert sum("duplicate_request" in r for r in results) == 2
        assert _user_turns(client, user_id) == 1
        assert _chats_used(client, user_id) == 1  # the two losers gave their chat back


@pytest.mark.concurrency
def test_a_stampede_of_copies_still_means_one_turn_one_chat_and_no_server_error(
    settings: Settings,
) -> None:
    """More copies than the day's allowance: each reserves a chat before it can lose the race, so
    some are told the limit was reached instead of that they are duplicates. The invariants hold."""
    if not os.environ.get("RLS_TEST_POSTGRES_URL"):
        pytest.skip("RLS_TEST_POSTGRES_URL not set - Postgres race check skipped")
    with build_test_client(settings, agent_answer="done") as client:
        headers, user_id = sign_in(client, settings, tier=Tier.FREE)

        results = _send_copies(client, headers, 8, "race-2")

        assert sum("turn.end" in r for r in results) == 1
        for r in results:
            assert "turn.end" in r or "duplicate_request" in r or "chat_limit_reached" in r, r
        assert _user_turns(client, user_id) == 1
        assert _chats_used(client, user_id) == 1
