"""End-to-end proof that a real `POST /threads/{id}/steer` call actually reaches the running
`AgentLoop` and is folded into the SECOND Step's LLM call — not just that `steering.accepted`
fires (that already passes, per `tests/api/test_phase11_routes.py`).

Driven with the real ASGI SSE harness (`sse_harness.collect_sse`, which invokes the app's real
`__call__` and reads its response body incrementally — unlike `httpx.ASGITransport`, which
buffers the whole response and so cannot be used to act mid-stream): a scripted two-Step
`LLMClient` stub proposes one tool call on Step 1, then blocks until the test's `on_started` hook
has posted the real `steer` HTTP call through a second, ordinary `httpx.AsyncClient` request.
Step 2 then runs and the test asserts its `chat()` call actually received the steering text as a
new user-role message — not merely that the endpoint accepted the request.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import httpx

from accounts.auth import issue_access_token
from config.settings import Settings
from llm.base import (
    LLMResponse,
    Message,
    MessageRole,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.classifier import IntentClassifier
from orchestrator.models import Routing
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from skills.registry import SkillRegistry
from tests.api.conftest import _TEST_JWT_SECRET, _insert_user, build_test_app
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


async def _post_sse(  # noqa: PLR0913 — mirrors sse_harness.collect_sse's own keyword-only shape
    app: object,
    path: str,
    *,
    json_body: dict[str, object],
    headers: dict[str, str],
    until: Callable[[list[str]], bool],
    timeout_s: float = 10.0,
    on_started: Callable[[], Awaitable[None]] | None = None,
) -> list[str]:
    """The `POST`-with-body analogue of `sse_harness.collect_sse`: drives a real streaming
    `POST` against the ASGI `app` directly (real routing, real dependency resolution, the real
    route handler and its `StreamingResponse` generator), reading the response body
    incrementally rather than buffering it whole — `httpx.ASGITransport`/`TestClient` both
    buffer the entire response and so cannot be acted on mid-stream, which is exactly what this
    test needs (posting a real `steer` request while `/agent/query`'s stream is still open)."""
    body_bytes = json.dumps(json_body).encode()
    header_list: list[tuple[bytes, bytes]] = [
        (b"host", b"test"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body_bytes)).encode()),
    ]
    header_list.extend((k.lower().encode(), v.encode()) for k, v in headers.items())
    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": header_list,
        "server": ("test", 80),
        "client": ("test", 12345),
    }

    disconnect = asyncio.Event()
    sent_body = asyncio.Event()
    response_body = bytearray()

    async def receive() -> dict[str, object]:
        if not sent_body.is_set():
            sent_body.set()
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes | bytearray):
                response_body.extend(chunk)

    async def _run() -> None:
        await app(scope, receive, send)  # type: ignore[operator]

    task = asyncio.create_task(_run())
    try:
        if on_started is not None:
            await on_started()
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            lines = response_body.decode("utf-8", errors="ignore").splitlines()
            if until(lines):
                break
            await asyncio.sleep(0.02)
    finally:
        disconnect.set()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, timeout=2)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return response_body.decode("utf-8", errors="ignore").splitlines()


class _SingleChipFlowClassifier(IntentClassifier):
    """Routes every message to the `single` framework against `chip-flow-review` — a real skill
    whose allowlist includes `get_canonical_context`, so the Step-1 tool call actually dispatches
    through the real tool/entitlement path, not a stub tool."""

    def __init__(self, registry: SkillRegistry) -> None:
        super().__init__(registry)

    async def _classify_inner(self, message: str) -> Routing:
        del message
        return Routing(framework="single", skills=("chip-flow-review",), reason="test")


class _TwoStepClient:
    """A scripted two-Step `LLMClient`: Step 1 proposes a tool call, Step 2 answers. Every call
    is recorded in `.calls` so the test can assert exactly what context Step 2's `chat()` saw —
    the load-bearing assertion that a steering message injected between Steps 1 and 2 actually
    reached the model, not just that the endpoint accepted it."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.step_one_done = asyncio.Event()
        # Step 1 blocks returning its tool-call proposal until the test confirms the real
        # `steer` HTTP call landed and was queued — the deterministic way to guarantee it is
        # queued before Step 2's `chat()` runs, with no race against how fast the (networkless)
        # stub turn otherwise completes.
        self.steered = asyncio.Event()

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del tools, native_web_search
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            self.step_one_done.set()
            await asyncio.wait_for(self.steered.wait(), timeout=5.0)
            return LLMResponse(
                text=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="get_canonical_context",
                        arguments={"id": "2330", "market": "TW"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
                usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            )
        return LLMResponse(
            text="chip context after steering. informational only.",
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


def _build_tool_registry():
    symbol = Symbol(id="2330", market=Market.TW)
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat — recorded fixture, not live",
        payload={"bar": {"symbol": "2330", "market": "TW", "trade_date": "2024-01-02"}},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _ = make_router(symbol, dict.fromkeys(Capability, result))
    return build_default_tool_registry(router)


async def test_steer_between_steps_reaches_second_step_llm_call() -> None:
    settings = Settings(jwt_secret_key=_TEST_JWT_SECRET, env="development")
    two_step_client = _TwoStepClient()
    app = build_test_app(
        settings,
        agent_answer="unused — the scripted client below always wins",
        tool_registry=_build_tool_registry(),
        agent_client=two_step_client,
        classifier=_SingleChipFlowClassifier(SkillRegistry()),
    )

    state = app.state.app_state
    user_id = await _insert_user(state.session_factory, tier=Tier.FREE)
    headers = {"Authorization": f"Bearer {issue_access_token(user_id=user_id, settings=settings)}"}

    # A plain (non-streaming) client for the two ordinary request/response calls: creating the
    # thread up front, and posting the real `steer` request once Step 1 is in flight.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_thread = await client.post(
            "/api/v1/threads", json={"title": "steer test"}, headers=headers
        )
        assert create_thread.status_code == 201
        thread_id = create_thread.json()["id"]

    steered_message = "STEERING: please also mention 2317 in the same breath."

    async def _steer_once_step_one_is_in_flight() -> None:
        await asyncio.wait_for(two_step_client.step_one_done.wait(), timeout=5.0)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as steer_client:
            resp = await steer_client.post(
                f"/api/v1/threads/{thread_id}/steer",
                json={"message": steered_message},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
        # Now that the real endpoint has queued the message on the live turn handle, let Step 1's
        # `chat()` call return — the loop dispatches the tool call, loops back to the next Step
        # boundary, drains the (already-queued) steering message, and calls Step 2 with it folded
        # in.
        two_step_client.steered.set()

    lines = await _post_sse(
        app,
        "/api/v1/agent/query",
        json_body={"message": "chip flows for 2330", "thread_id": thread_id},
        headers=headers,
        until=lambda ls: any(line.startswith("event: turn.end") for line in ls),
        on_started=_steer_once_step_one_is_in_flight,
        timeout_s=10.0,
    )
    del lines  # the load-bearing assertions are on `two_step_client.calls`, not the SSE frames

    assert len(two_step_client.calls) == 2
    step_two_messages = two_step_client.calls[1]
    step_two_text = " ".join(m.content or "" for m in step_two_messages)
    assert steered_message in step_two_text
    # It was injected as a distinct user-role message, not merged into the system prompt.
    injected = [
        m for m in step_two_messages if m.role is MessageRole.USER and m.content == steered_message
    ]
    assert len(injected) == 1
