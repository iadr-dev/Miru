"""Characterization of `api.turn_driver._drive_turn`: the ordered events and collaborator calls of
every turn outcome (done, deep research, memory recall, BYOK estimate, cancel, orchestrator error,
setup error, save failure). Collaborators are replaced at the module seam; no database, no LLM."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from api import turn_driver
from events.kinds import EventKind
from events.usage import ModelUsage, TurnUsage
from orchestrator.models import OrchestratorResult, Routing
from orchestrator.orchestrator import PersonalityContext


class _Bus:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log

    def publish(
        self,
        kind: EventKind,
        *,
        turn_id: str | None = None,
        agent_id: str | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        self.log.append(("publish", kind, turn_id, agent_id, data))


class _Session:
    pass


def _result(*, usage: TurnUsage | None = None) -> OrchestratorResult:
    return OrchestratorResult(
        final_text="the answer", routing=Routing(framework="none", reason="t"), usage=usage
    )


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.result = _result()
        self.run_error: BaseException | None = None
        self.persist_error: Exception | None = None
        self.pet_error: Exception | None = None
        self.memory_on = True
        self.recalled: list[Any] = []
        self.recall_block: str | None = "recalled block"
        self.recall_calls: list[Any] = []
        self.run_kwargs: dict[str, Any] = {}
        self.recall_return: object = None
        self.persisted: list[OrchestratorResult] = []
        self.deep_kwargs: dict[str, Any] = {}
        self._patch(monkeypatch)

    def _patch(self, mp: pytest.MonkeyPatch) -> None:
        log = self.log

        @contextlib.asynccontextmanager
        async def unit(_state: object) -> AsyncIterator[_Session]:
            log.append(("unit",))
            yield _Session()

        async def pet_hint(_session: object, *, user_id: str) -> str | None:
            log.append(("pet_hint", user_id))
            if self.pet_error is not None:
                raise self.pet_error
            return "pet-tone"

        async def load_history(_store: object, **kw: str) -> tuple[str, ...]:
            log.append(("load_history", kw))
            return ("h1",)

        async def deep(**kw: Any) -> OrchestratorResult:
            self.deep_kwargs = kw
            log.append(("deep_research",))
            return self.result

        async def memory_enabled(_session: object, *, user_id: str) -> bool:
            log.append(("memory_enabled", user_id))
            return self.memory_on

        class MemoryStore:
            def __init__(self, _session: object) -> None: ...

            async def mark_used(self, *, user_id: str, memory_ids: list[str]) -> None:
                log.append(("mark_used", user_id, memory_ids))

        async def recall(**kw: Any) -> tuple[str | None, list[Any]]:
            self.recall_calls.append(kw)
            return self.recall_block, self.recalled

        async def persist(_store: object, **kw: Any) -> None:
            log.append(("persist", kw["user_id"], kw["thread_id"], kw["parent_message_id"]))
            log.append(("persist_regenerate_of", kw["regenerate_of"]))
            self.persisted.append(kw["result"])
            if self.persist_error is not None:
                raise self.persist_error

        async def title(_state: object, **kw: str) -> None:
            log.append(("title", kw))

        async def record_usage(_session: object, **kw: Any) -> None:
            log.append(("record_usage", kw["user_id"], kw["turn_id"], kw["byok"]))

        async def reward(_session: object, **kw: Any) -> None:
            log.append(("reward", kw["turn_id"], kw["user_id"]))

        mp.setattr(turn_driver, "_unit", unit)
        mp.setattr(turn_driver, "pet_hint_for_user", pet_hint)
        mp.setattr(turn_driver, "_load_history", load_history)
        mp.setattr(turn_driver, "run_deep_research_turn", deep)
        mp.setattr(turn_driver, "memory_enabled", memory_enabled)
        mp.setattr(turn_driver, "SqlAlchemyMemoryStore", MemoryStore)
        mp.setattr(turn_driver, "WatchlistStore", lambda _s: object())
        mp.setattr(turn_driver, "SqlAlchemyConversationStore", lambda _s: object())
        mp.setattr(turn_driver, "recall_with_ids", recall)
        mp.setattr(turn_driver, "_persist_answer", persist)
        mp.setattr(turn_driver, "_maybe_title_thread", title)
        mp.setattr(turn_driver, "_record_usage", record_usage)
        mp.setattr(turn_driver, "reward_completed_chat", reward)

    def state(self) -> Any:
        log = self.log

        async def run(message: str, **kw: Any) -> OrchestratorResult:
            self.run_kwargs = {"message": message, **kw}
            log.append(("orchestrator_run",))
            if "recall_provider" in kw and self.recall_return == "call":
                self.recall_return = await kw["recall_provider"]()
            if self.run_error is not None:
                raise self.run_error
            return self.result

        @contextlib.asynccontextmanager
        async def session_factory() -> AsyncIterator[_Session]:
            log.append(("session_factory",))
            yield _Session()

        def resolve(**kw: Any) -> Any:
            log.append(("resolve_personality", kw))
            return SimpleNamespace(id="tone-1")

        return SimpleNamespace(
            personality_registry=SimpleNamespace(resolve=resolve),
            turn_registry=SimpleNamespace(
                get=lambda tid: log.append(("registry_get", tid)) or "RUN"
            ),
            orchestrator=SimpleNamespace(run=run),
            session_factory=session_factory,
        )

    async def drive(self, **overrides: Any) -> None:
        kwargs: dict[str, Any] = {
            "state": self.state(),
            "event_bus": _Bus(self.log),
            "user_id": "u1",
            "thread_id": "th1",
            "turn_id": "t1",
            "message": "hello",
            "ctx": "CTX",
            "personality": PersonalityContext(user_preference="calm"),
            "answer_parent_message_id": "parent-1",
        }
        kwargs.update(overrides)
        await turn_driver._drive_turn(**kwargs)

    def events(self) -> list[tuple[EventKind, str | None, dict[str, object] | None]]:
        return [(e[1], e[3], e[4]) for e in self.log if e[0] == "publish"]

    def kinds(self) -> list[EventKind]:
        return [e[1] for e in self.log if e[0] == "publish"]

    def steps(self) -> list[str]:
        return [str(e[0]) if e[0] != "publish" else f"publish:{e[1].value}" for e in self.log]


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


async def test_a_normal_turn_runs_the_full_lifecycle_in_order(h: _Harness) -> None:
    await h.drive(regenerate_of="old-turn")

    assert h.steps() == [
        "publish:turn.start",
        "resolve_personality",
        "publish:personality.used",
        "registry_get",
        "unit",
        "pet_hint",
        "unit",
        "load_history",
        "orchestrator_run",
        "unit",
        "persist",
        "persist_regenerate_of",
        "title",
        "session_factory",
        "record_usage",
        "publish:message",
        "session_factory",
        "reward",
        "publish:turn.end",
    ]
    events = h.events()
    assert events[0] == (EventKind.TURN_START, None, {"thread_id": "th1"})
    assert events[1] == (EventKind.PERSONALITY_USED, None, {"personality": "tone-1"})
    assert events[2][2] == {"text": "the answer", "framework": "none"}
    assert events[3][2] == {"status": "done"}
    assert ("persist", "u1", "th1", "parent-1") in h.log
    assert ("persist_regenerate_of", "old-turn") in h.log
    assert ("record_usage", "u1", "t1", False) in h.log
    assert ("reward", "t1", "u1") in h.log
    assert ("title", {"user_id": "u1", "thread_id": "th1", "message": "hello"}) in h.log
    resolve_kwargs = next(e[1] for e in h.log if e[0] == "resolve_personality")
    assert resolve_kwargs == {
        "turn_override": None,
        "thread_override": None,
        "user_preference": "calm",
    }


async def test_the_orchestrator_gets_the_turn_inputs_and_the_pet_hint(h: _Harness) -> None:
    await h.drive(
        model_override="m-x", web_search_enabled=True, locale="en", images=("img",), byok=None
    )

    kw = h.run_kwargs
    assert kw["message"] == "hello"
    assert kw["tool_context"] == "CTX"
    assert kw["turn_id"] == "t1"
    assert kw["personality"] == PersonalityContext(user_preference="calm", pet_hint="pet-tone")
    assert kw["steering_source"] == "RUN"
    assert kw["model_override"] == "m-x"
    assert kw["web_search_enabled"] is True
    assert kw["locale"] == "en"
    assert kw["images"] == ("img",)
    assert kw["history"] == ("h1",)
    assert callable(kw["recall_provider"])


async def test_deep_research_replaces_the_orchestrator(h: _Harness) -> None:
    await h.drive(deep_research=True, model_override="m", locale="en", images=("i",))

    assert "orchestrator_run" not in h.steps()
    assert "deep_research" in h.steps()
    kw = h.deep_kwargs
    assert kw["question"] == "hello"
    assert kw["turn_id"] == "t1"
    assert kw["locale"] == "en"
    assert kw["model_override"] == "m"
    assert kw["history"] == ("h1",)
    assert kw["images"] == ("i",)
    assert h.kinds()[-1] is EventKind.TURN_END
    assert h.events()[-1][2] == {"status": "done"}


def _memory(i: int, text: str) -> Any:
    return SimpleNamespace(
        id=f"m{i}", key=f"k{i}", type=SimpleNamespace(value="fact"), content={"text": text}
    )


async def test_recall_records_the_memories_on_the_persisted_result(h: _Harness) -> None:
    h.recalled = [_memory(1, "likes  TSMC"), _memory(2, "x" * 100)]
    h.recall_return = "call"

    await h.drive()

    assert h.recall_return == "recalled block"
    assert ("memory_enabled", "u1") in h.log
    assert ("mark_used", "u1", ["m1", "m2"]) in h.log
    used = [e for e in h.events() if e[0] is EventKind.MEMORY_USED]
    assert len(used) == 1
    memories = used[0][2]["memories"]  # type: ignore[index]
    assert memories[0] == {"id": "m1", "key": "k1", "type": "fact", "text": "likes TSMC"}
    assert memories[1]["text"] == "x" * 80 + "…"
    assert h.recall_calls[0]["query"] == "hello"
    assert h.recall_calls[0]["thread_id"] == "th1"
    persisted = h.persisted[0].usage
    assert persisted is not None
    assert [m.id for m in persisted.memories_used] == ["m1", "m2"]
    assert persisted.personality == "tone-1"


async def test_recall_with_nothing_used_publishes_nothing_and_marks_nothing(h: _Harness) -> None:
    h.recalled = []
    h.recall_return = "call"

    await h.drive()

    assert h.recall_return == "recalled block"
    assert not [e for e in h.log if e[0] == "mark_used"]
    assert EventKind.MEMORY_USED not in h.kinds()
    persisted = h.persisted[0].usage
    assert persisted is not None
    assert persisted.memories_used == ()


async def test_paused_memory_recalls_nothing(h: _Harness) -> None:
    h.memory_on = False
    h.recall_return = "call"

    await h.drive()

    assert h.recall_return is None
    assert h.recall_calls == []
    assert EventKind.MEMORY_USED not in h.kinds()


async def test_a_byok_turn_resends_the_final_usage_with_the_list_price_estimate(
    h: _Harness,
) -> None:
    model = ModelUsage(
        provider="openai",
        model="gpt-5.4-mini",
        calls=1,
        input_tokens=1000,
        output_tokens=100,
        cost_micros=0,
    )
    h.result = _result(usage=TurnUsage(turn_id="t1", models=(model,)))

    await h.drive(byok=object())

    usage_events = [e for e in h.events() if e[0] is EventKind.USAGE]
    assert len(usage_events) == 1
    assert usage_events[0][1] == "root"
    assert usage_events[0][2]["estimated_cost_micros"] > 0  # type: ignore[index]
    assert h.steps().index("publish:usage") < h.steps().index("persist")
    assert ("record_usage", "u1", "t1", True) in h.log
    assert h.persisted[0].usage is not None
    assert h.persisted[0].usage.estimated_cost_micros > 0


async def test_a_platform_key_turn_sends_no_extra_usage_frame(h: _Harness) -> None:
    await h.drive()

    assert EventKind.USAGE not in h.kinds()


async def test_a_cancel_ends_the_stream_with_a_cancelled_frame_and_propagates(
    h: _Harness,
) -> None:
    h.run_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await h.drive()

    assert h.events()[-1] == (EventKind.TURN_END, None, {"status": "cancelled"})
    assert "persist" not in h.steps()
    assert "reward" not in h.steps()


async def test_an_orchestrator_failure_ends_with_an_error_frame_and_saves_nothing(
    h: _Harness,
) -> None:
    h.run_error = RuntimeError("model exploded")

    await h.drive()

    assert h.events()[-1] == (
        EventKind.TURN_END,
        None,
        {"status": "error", "message": "model exploded"},
    )
    assert h.steps()[-1] == "publish:turn.end"
    for step in ("persist", "title", "record_usage", "reward", "publish:message"):
        assert step not in h.steps()


async def test_a_failure_before_the_orchestrator_is_also_an_error_frame(h: _Harness) -> None:
    h.pet_error = ValueError("pet store down")

    await h.drive()

    assert "orchestrator_run" not in h.steps()
    assert h.events()[-1] == (
        EventKind.TURN_END,
        None,
        {"status": "error", "message": "pet store down"},
    )


async def test_a_save_failure_says_so_and_skips_title_usage_and_reward(h: _Harness) -> None:
    h.persist_error = RuntimeError("db gone")

    await h.drive()

    assert h.events()[-1] == (
        EventKind.TURN_END,
        None,
        {"status": "error", "message": "The answer could not be saved."},
    )
    for step in ("title", "record_usage", "reward", "publish:message"):
        assert step not in h.steps()
