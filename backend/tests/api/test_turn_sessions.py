"""A chat turn holds no database transaction while the model works.

Before: the request's session loaded the caller (users, subscriptions), appended the question and
then stayed in that one open transaction through the classifier, every agent and tool call and the
title call, committing only after the answer was saved. Each running turn pinned a pooled
connection "idle in transaction" with locks on the thread row, users and subscriptions (about 20
concurrent chats emptied the pool), the question was not durable until the answer was, and the
server's idle-in-transaction timeout ended any turn longer than that before its answer was saved.
The reconnect stream (`GET /threads/{id}/stream`) held the same kind of transaction for as long as
it stayed open."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from config.settings import Settings
from conversation.store import SqlAlchemyConversationStore
from db import rls
from db.models.conversation import TurnRow
from orchestrator.models import OrchestratorResult, Routing
from schemas.enums import Tier
from tests.api.conftest import _TEST_JWT_SECRET, build_test_client, sign_in
from tests.api.sse_harness import collect_sse


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")


class _Tracking:
    """Wraps the app's session factory and remembers every session it hands out."""

    def __init__(self, real: object) -> None:
        self._real = real
        self.sessions: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        session = self._real(*args, **kwargs)  # type: ignore[operator]
        self.sessions.append(session)
        return session

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def open_transactions(self) -> list[object]:
        return [s for s in self.sessions if s.in_transaction()]  # type: ignore[attr-defined]


class _SlowOrchestrator:
    """Stands in for the model: recalls memory like the real root agent, then waits until the test
    lets it answer."""

    def __init__(self) -> None:
        self.thinking = asyncio.Event()
        self.answer = asyncio.Event()

    async def run(self, message: str, **kwargs: object) -> OrchestratorResult:
        recall = kwargs.get("recall_provider")
        if callable(recall):
            await recall()
        self.thinking.set()
        await self.answer.wait()
        return OrchestratorResult(
            final_text=f"answer to {message}",
            routing=Routing(framework="single", skills=(), reason="test"),
        )


def _setup(client, settings: Settings):  # type: ignore[no-untyped-def]
    headers, user_id = sign_in(client, settings, tier=Tier.FREE)
    state = client.app.state.app_state
    real_factory = state.session_factory
    tracking = _Tracking(real_factory)
    object.__setattr__(state, "session_factory", tracking)
    orchestrator = _SlowOrchestrator()
    object.__setattr__(state, "orchestrator", orchestrator)
    return headers, user_id, real_factory, tracking, orchestrator


async def _turn_count(factory, user_id: str) -> int:  # type: ignore[no-untyped-def]
    # The test inspects as the system (no user is bound in its own context; with row-level
    # security on, the rows would otherwise be invisible to it).
    with rls.system_access():
        return await _count(factory, user_id)


async def _count(factory, user_id: str) -> int:  # type: ignore[no-untyped-def]
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(TurnRow).where(TurnRow.user_id == user_id)
                )
            ).scalar_one()
        )


def test_a_running_turn_holds_no_open_transaction_and_its_question_is_already_saved(
    settings: Settings,
) -> None:
    with build_test_client(settings, agent_answer="x") as client:
        headers, user_id, real_factory, tracking, orchestrator = _setup(client, settings)
        observed: dict[str, object] = {}

        async def while_the_model_works() -> None:
            await asyncio.wait_for(orchestrator.thinking.wait(), timeout=5)
            observed["open"] = tracking.open_transactions()
            observed["turns_while_running"] = await _turn_count(real_factory, user_id)
            orchestrator.answer.set()

        assert client.portal is not None
        lines = client.portal.call(
            lambda: collect_sse(
                client.app,
                "/api/v1/agent/query",
                method="POST",
                json_body={"message": "hello"},
                until=lambda ls: "event: turn.end" in ls,
                timeout_s=10,
                on_started=while_the_model_works,
                headers=headers,
            )
        )

        assert observed["open"] == []  # nothing is held while the model works
        assert observed["turns_while_running"] == 1  # the question is durable already
        assert "event: turn.end" in lines
        assert client.portal.call(lambda: _turn_count(real_factory, user_id)) == 2  # + the answer
        assert tracking.open_transactions() == []


def test_the_reconnect_stream_holds_no_open_transaction(settings: Settings) -> None:
    with build_test_client(settings, agent_answer="x") as client:
        headers, user_id, real_factory, tracking, orchestrator = _setup(client, settings)

        async def create_thread() -> str:
            with rls.system_access():
                async with real_factory() as session:
                    store = SqlAlchemyConversationStore(session)
                    thread = await store.create_thread(user_id=user_id)
                    await session.commit()
                    return thread.id

        assert client.portal is not None
        thread_id = client.portal.call(create_thread)
        observed: dict[str, object] = {}

        async def while_the_model_works() -> None:
            await asyncio.wait_for(orchestrator.thinking.wait(), timeout=5)

            before = len(tracking.sessions)

            async def reconnected() -> None:
                # The reconnect has resolved the caller and read the thread (one short session
                # each), then a moment more so it is streaming.
                for _ in range(250):
                    if len(tracking.sessions) >= before + 2:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
                observed["reconnect_sessions"] = len(tracking.sessions) - before
                observed["open"] = tracking.open_transactions()
                orchestrator.answer.set()

            observed["resumed"] = await collect_sse(
                client.app,
                f"/api/v1/threads/{thread_id}/stream",
                until=lambda ls: "event: turn.end" in ls,
                timeout_s=10,
                on_started=reconnected,
                headers=headers,
            )

        client.portal.call(
            lambda: collect_sse(
                client.app,
                "/api/v1/agent/query",
                method="POST",
                json_body={"message": "hello", "thread_id": thread_id},
                until=lambda ls: "event: turn.end" in ls,
                timeout_s=15,
                on_started=while_the_model_works,
                headers=headers,
            )
        )

        assert observed["reconnect_sessions"] >= 1  # it did resolve the caller through a session
        assert observed["open"] == []
        assert "event: turn.end" in observed["resumed"]  # type: ignore[operator]
