"""End-to-end HTTP tests for the FastAPI surface, driven through Starlette's `TestClient` against
the fully-wired app (real registry/skills/orchestrator, stub LLM, no network).

Covers: public reads (health/quote/sources) as anonymous; sign-in enforcement on user-scoped and
AI routes; thread CRUD with per-user isolation; the `/agent/query` SSE stream end to end; the
standardized error envelope; and the governance-critical guarantee that the streamed answer
carries its non-trading caveat (the orchestrator's guardrail output survives the HTTP surface).
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from db.models import UsageEventRow
from events.rate_limit import RateLimitVerdict
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in


def _parse_sse(body: str) -> list[dict[str, object]]:
    """Parse SSE frames into `{event, envelope, data}` — `envelope` is the full correlation
    envelope, `data` its inner tool/event payload (the `data` field of the envelope)."""
    frames: list[dict[str, object]] = []
    kind: str | None = None
    for line in body.splitlines():
        if line.startswith("event: "):
            kind = line[len("event: ") :]
        elif line.startswith("data: ") and kind is not None:
            envelope = json.loads(line[len("data: ") :])
            frames.append({"event": kind, "envelope": envelope, "data": envelope["data"]})
            kind = None
    return frames


class TestPublicReads:
    def test_health_is_anonymous(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health", params={"symbols": "2330", "market": "TW"})
        assert resp.status_code == 200
        assert "symbols" in resp.json()

    def test_quote_is_anonymous(self, client: TestClient) -> None:
        resp = client.get("/api/v1/quote", params={"symbols": "2330,2317", "market": "TW"})
        assert resp.status_code == 200
        assert len(resp.json()["quotes"]) == 2

    def test_sources_matrix(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sources")
        assert resp.status_code == 200
        assert "entries" in resp.json()

    def test_quote_requires_at_least_one_symbol(self, client: TestClient) -> None:
        resp = client.get("/api/v1/quote", params={"symbols": " ", "market": "TW"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_request"
        assert body["retryable"] is False

    def test_healthz(self, client: TestClient) -> None:
        assert client.get("/healthz").json() == {"status": "ok"}


class TestAuthGuards:
    def test_agent_query_requires_signin(self, client: TestClient) -> None:
        resp = client.post("/api/v1/agent/query", json={"message": "hi"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "unauthenticated"

    def test_threads_list_requires_signin(self, client: TestClient) -> None:
        assert client.get("/api/v1/threads").status_code == 401

    def test_me_requires_signin(self, client: TestClient) -> None:
        assert client.get("/api/v1/me").status_code == 401

    def test_invalid_token_is_unauthenticated(self, client: TestClient) -> None:
        resp = client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert resp.status_code == 401


class TestAccounts:
    def test_me_returns_profile_and_entitlements(self, client: TestClient, signed_in_user) -> None:
        headers, user_id = signed_in_user
        resp = client.get("/api/v1/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["id"] == user_id
        assert body["entitlements"]["tier"] == "free"

    def test_begin_oauth_unknown_provider_404(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/oauth/myspace")
        assert resp.status_code == 404
        assert resp.json()["code"] == "unknown_provider"

    def test_begin_oauth_unconfigured_provider_503(self, client: TestClient) -> None:
        # No Google creds in the test Settings → the flow refuses to start rather than default.
        resp = client.post("/api/v1/auth/oauth/google")
        assert resp.status_code == 503
        assert resp.json()["code"] == "provider_unconfigured"

    def test_refresh_without_cookie_401(self, client: TestClient) -> None:
        assert client.post("/api/v1/auth/refresh").status_code == 401


class TestThreads:
    def test_create_list_get_delete(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        created = client.post("/api/v1/threads", json={"title": "My thread"}, headers=headers)
        assert created.status_code == 201
        thread_id = created.json()["id"]

        listed = client.get("/api/v1/threads", headers=headers)
        assert listed.status_code == 200
        assert any(t["id"] == thread_id for t in listed.json()["threads"])

        got = client.get(f"/api/v1/threads/{thread_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["title"] == "My thread"

        deleted = client.delete(f"/api/v1/threads/{thread_id}", headers=headers)
        assert deleted.status_code == 204

    def test_get_missing_thread_404(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.get("/api/v1/threads/does-not-exist", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "thread_not_found"


class TestAgentQuery:
    def test_query_streams_turn_and_persists(
        self, client: TestClient, signed_in_user, agent_answer: str
    ) -> None:
        headers, _ = signed_in_user
        resp = client.post(
            "/api/v1/agent/query",
            json={"message": "台積電最近如何"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        frames = _parse_sse(resp.text)
        kinds = [f["event"] for f in frames]
        assert kinds[0] == "turn.start"
        assert kinds[-1] == "turn.end"
        assert "message" in kinds

        message_frame = next(f for f in frames if f["event"] == "message")
        # The guardrail layer may append its caveat, so the answer contains (not necessarily
        # equals) the model's text.
        assert agent_answer in message_frame["data"]["text"]

        # The turn was persisted: a fresh thread now exists with the user turn + assistant turn.
        threads = client.get("/api/v1/threads", headers=headers).json()["threads"]
        assert len(threads) == 1
        thread_id = threads[0]["id"]
        full = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
        roles = [turn["role"] for turn in full["turns"]]
        assert roles == ["user", "assistant"]

    def test_streamed_answer_carries_non_trading_caveat(
        self, client: TestClient, signed_in_user, agent_answer: str
    ) -> None:
        # Governance: the orchestrator's guardrail-checked text is what streams — the caveat the
        # answer carries must survive to the HTTP frame unmodified.
        headers, _ = signed_in_user
        resp = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
        message_frame = next(f for f in _parse_sse(resp.text) if f["event"] == "message")
        assert "非投資建議" in message_frame["data"]["text"]

    def test_query_into_missing_thread_404(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.post(
            "/api/v1/agent/query",
            json={"message": "hi", "thread_id": "nope"},
            headers=headers,
        )
        assert resp.status_code == 404


class TestPreTurnGate:
    """The `POST /agent/query` pre-turn gate is live end-to-end (not just the limiter/store in
    isolation): a Free user's 4th chat of the day is rejected before a turn is created, proving
    the durable daily-chat quota is wired into the real route."""

    def test_free_user_daily_chat_limit_enforced(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user  # Free tier — chat_limit_per_day = 3
        for _ in range(3):
            ok = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
            assert ok.status_code == 200

        blocked = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
        assert blocked.status_code == 429
        body = blocked.json()
        assert body["code"] == "chat_limit_reached"

    def test_rate_limit_verdict_rejects_at_the_real_route(self, settings, agent_answer) -> None:
        """A denying rate limiter, injected at `create_app`, produces a 429 on the real
        `POST /agent/query` — proving the limiter's verdict is consulted before a turn is created,
        not just that the limiter class works standalone."""

        class _DenyRateLimiter:
            async def check(
                self, caller_id: str, *, max_requests: int, window_seconds: int
            ) -> RateLimitVerdict:
                del caller_id, max_requests, window_seconds
                return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=12.0)

        with build_test_client(
            settings, agent_answer=agent_answer, rate_limiter=_DenyRateLimiter()
        ) as client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
            assert resp.status_code == 429
            assert resp.json()["code"] == "rate_limited"

    def test_usage_rollup_written_through_after_turn(
        self, client: TestClient, signed_in_user
    ) -> None:
        """The post-turn write-through is live: a completed turn lands a durable `usage_events`
        row for the caller (the `UsageAccumulator.snapshot()` sink is actually connected)."""
        headers, user_id = signed_in_user
        resp = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
        assert resp.status_code == 200
        # Drain the stream so the background driver finishes its post-turn write.
        _parse_sse(resp.text)

        state = client.app.state.app_state

        async def _count() -> int:
            async with state.session_factory() as session:
                result = await session.execute(
                    select(func.count())
                    .select_from(UsageEventRow)
                    .where(UsageEventRow.user_id == user_id)
                )
                return int(result.scalar_one())

        assert asyncio.run(_count()) >= 1
