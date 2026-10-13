"""Deep research end to end over HTTP: the Pro gate, the streamed progress lanes, and a cited,
governance-scanned report - with a scripted model and a fake web, so no provider or network is
touched."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api import deep_research_turn
from config.settings import Settings
from dataaccess.web_search import WebSearchGateway
from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
)
from schemas.enums import DataClass, Tier, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client, sign_in
from tests.api.test_routes import _parse_sse

pytestmark = pytest.mark.governance

_PAGE = "https://news.example.com/tsmc-revenue"


class _ResearchClient:
    """Answers each research step by recognising its system prompt."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self, claim: str = "September revenue rose 12% year on year.") -> None:
        self._claim = claim
        self.seen: list[tuple[str, str]] = []  # (system, user) of every call

    def asked(self, system_part: str) -> list[str]:
        return [user for system, user in self.seen if system_part in system]

    async def chat(
        self, messages: list[Message], tools: object = None, *, native_web_search: bool = False
    ) -> LLMResponse:
        del tools, native_web_search
        system = messages[0].content or ""
        self.seen.append((system, messages[-1].content or ""))
        if "Describe what the attached image" in system:
            return LLMResponse(
                text="A candlestick chart of 2330 for September.",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            )
        if "Rewrite the user's latest message" in system:
            body: dict[str, object] = {"question": "台積電第四季資本支出是多少？"}
        elif "plan research" in system:
            body = {"sub_questions": [{"question": "revenue", "queries": ["tsmc revenue"]}]}
        elif "review research progress" in system:
            body = {"sufficient": True}
        elif "research report" not in system:
            return LLMResponse(
                text="一般對話回答。以上為資訊整理，非投資建議。",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            )
        else:
            claim = {"text": self._claim, "source_ids": [1]}
            body = {
                "title": "TSMC revenue",
                "summary": [claim],
                "sections": [{"heading": "Revenue", "claims": [claim]}],
                "open_questions": ["Next-quarter guidance"],
            }
        return LLMResponse(
            text=json.dumps(body),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )

    async def stream(self, messages, tools=None, *, native_web_search=False):  # type: ignore[no-untyped-def]
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        yield TextDeltaEvent(text=response.text or "")
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return 1


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="WEB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="test",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


class _FakeGateway(WebSearchGateway):
    def __init__(self, *, has_results: bool = True) -> None:
        self._has_results = has_results

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        hits = (
            [{"title": "TSMC monthly revenue", "url": _PAGE, "snippet": "", "published": None}]
            if self._has_results
            else []
        )
        return _result({"hits": hits})

    async def fetch_page(
        self, url: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, resolver=None
    ):  # type: ignore[no-untyped-def,override]
        return _result({"url": url, "title": "TSMC monthly revenue", "text": "Revenue rose 12%."})


@pytest.fixture
def stub() -> _ResearchClient:
    return _ResearchClient()


@pytest.fixture
def research_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, stub: _ResearchClient
) -> Iterator[TestClient]:
    monkeypatch.setattr(deep_research_turn, "build_gateway", _FakeGateway)
    with build_test_client(settings, agent_answer="x", agent_client=stub) as client:
        yield client


def _ask(client: TestClient, headers: dict[str, str], *, deep: bool = True):
    return client.post(
        "/api/v1/agent/query",
        json={"message": "台積電最近營收如何？", "deep_research": deep},
        headers=headers,
    )


def test_only_pro_may_run_deep_research(research_client: TestClient, settings: Settings) -> None:
    for tier in (Tier.FREE, Tier.PREMIUM):
        headers, _ = sign_in(research_client, settings, tier=tier)
        resp = _ask(research_client, headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "feature_not_entitled"


def test_a_pro_report_is_cited_and_streams_progress(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    resp = _ask(research_client, headers)
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)

    activities = [f["data"]["activity"] for f in frames if f["event"] == "agent.spawned"]
    assert activities[0] == "規劃研究方向"
    assert "[1] 搜尋：tsmc revenue" in activities  # lane 1 of the sub-questions
    assert activities[-1] == "撰寫研究報告"
    assert sum(f["event"] == "agent.done" for f in frames) == len(activities)

    sources = next(f for f in frames if f["event"] == "web_search.result")["data"]["sources"]
    assert sources == [{"url": _PAGE, "title": "TSMC monthly revenue"}]

    text = next(f for f in frames if f["event"] == "message")["data"]["text"]
    assert "September revenue rose 12% year on year. [1]" in text
    assert f"1. [TSMC monthly revenue]({_PAGE})" in text
    assert "recommendation" in text or "建議" in text  # the standard non-trading caveat
    assert frames[-1]["data"]["status"] == "done"


def test_there_is_no_cap_on_how_many_reports_a_pro_user_may_start(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    for _ in range(6):  # no daily cap, and no hidden limit on starts either
        assert _ask(research_client, headers).status_code == 200


def test_a_run_that_finds_nothing_says_so(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deep_research_turn, "build_gateway", lambda: _FakeGateway(has_results=False)
    )
    with build_test_client(settings, agent_answer="x", agent_client=_ResearchClient()) as client:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        for _ in range(3):
            resp = _ask(client, headers)
            assert resp.status_code == 200
            text = next(f for f in _parse_sse(resp.text) if f["event"] == "message")["data"]["text"]
            assert "無法產出" in text


class _BrokenClient(_ResearchClient):
    async def chat(self, messages, tools=None, *, native_web_search=False):  # type: ignore[no-untyped-def,override]
        return LLMResponse(
            text="not json",
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def test_our_own_failure_says_no_report_was_produced(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deep_research_turn, "build_gateway", _FakeGateway)
    with build_test_client(settings, agent_answer="x", agent_client=_BrokenClient()) as client:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        resp = _ask(client, headers)
        assert resp.status_code == 200
        text = next(f for f in _parse_sse(resp.text) if f["event"] == "message")["data"]["text"]
        assert "未產出報告" in text
        assert "每日" not in text  # there is no daily count to refer to


def test_trading_language_in_a_model_claim_never_reaches_the_report(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deep_research_turn, "build_gateway", _FakeGateway)
    client_stub = _ResearchClient(claim="分析師建議買進台積電，目標價 1200 元。")
    with build_test_client(settings, agent_answer="x", agent_client=client_stub) as client:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        resp = _ask(client, headers)
        text = next(f for f in _parse_sse(resp.text) if f["event"] == "message")["data"]["text"]
        assert "買進" not in text
        assert "目標價" not in text


def _thread(client: TestClient, headers: dict[str, str]) -> dict:  # type: ignore[type-arg]
    thread_id = client.get("/api/v1/threads", headers=headers).json()["threads"][0]["id"]
    return client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()


def _last(thread: dict, role: str) -> dict:  # type: ignore[type-arg]
    return [t for t in thread["turns"] if t["role"] == role][-1]


def _message_text(resp_text: str) -> str:
    return next(f for f in _parse_sse(resp_text) if f["event"] == "message")["data"]["text"]


def test_regenerate_reruns_a_deep_research_turn_as_deep_research(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)
    thread = _thread(research_client, headers)
    assert _last(thread, "user")["mode"] == "deep_research"

    resp = research_client.post(
        f"/api/v1/threads/{thread['id']}/turns/{_last(thread, 'assistant')['id']}/regenerate",
        headers=headers,
    )
    assert resp.status_code == 200
    assert "### 來源" in _message_text(resp.text)  # a report again, not a plain chat answer


def test_regenerate_can_explicitly_drop_back_to_plain_chat(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)
    thread = _thread(research_client, headers)
    resp = research_client.post(
        f"/api/v1/threads/{thread['id']}/turns/{_last(thread, 'assistant')['id']}/regenerate",
        json={"deep_research": False},
        headers=headers,
    )
    assert "### 來源" not in _message_text(resp.text)


def test_a_plain_turn_regenerates_as_plain_chat(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers, deep=False)
    thread = _thread(research_client, headers)
    assert _last(thread, "user")["mode"] is None
    resp = research_client.post(
        f"/api/v1/threads/{thread['id']}/turns/{_last(thread, 'assistant')['id']}/regenerate",
        headers=headers,
    )
    assert "### 來源" not in _message_text(resp.text)


def test_editing_a_deep_research_question_keeps_it_deep_research(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)
    thread = _thread(research_client, headers)
    resp = research_client.post(
        f"/api/v1/threads/{thread['id']}/turns/{_last(thread, 'user')['id']}/edit",
        json={"message": "台積電資本支出呢？"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert "### 來源" in _message_text(resp.text)
    assert _last(_thread(research_client, headers), "user")["mode"] == "deep_research"


def test_token_usage_is_persisted_on_the_turn_and_shows_in_the_usage_endpoints(
    research_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)

    usage = _last(_thread(research_client, headers), "assistant")["usage"]
    assert usage["input_tokens"] > 0
    assert usage["models"][0]["model"] == "stub-model"

    llm = research_client.get("/api/v1/usage/llm?days=7", headers=headers).json()
    assert llm["totals"]["calls"] >= 3  # plan + review + write
    assert llm["models"][0]["provider"] == "stub"
    assert len(llm["daily"]) == 7

    today = research_client.get("/api/v1/usage/today", headers=headers).json()
    assert "deep_research_used" not in today
    assert "deep_research_limit" not in today
    assert today["chats_used"] == 0  # Pro chat is unlimited, so no chat counter is touched


def test_other_users_cannot_see_your_usage(research_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)
    other, _ = sign_in(research_client, settings, tier=Tier.PRO)
    llm = research_client.get("/api/v1/usage/llm", headers=other).json()
    assert llm["totals"]["calls"] == 0


def test_a_follow_up_is_researched_as_a_standalone_question(
    research_client: TestClient, settings: Settings, stub: _ResearchClient
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    _ask(research_client, headers)
    assert stub.asked("Rewrite the user's latest message") == []  # a first question stands alone

    thread = _thread(research_client, headers)
    resp = research_client.post(
        "/api/v1/agent/query",
        json={"message": "那資本支出呢？", "deep_research": True, "thread_id": thread["id"]},
        headers=headers,
    )
    assert resp.status_code == 200
    restated = stub.asked("Rewrite the user's latest message")
    assert len(restated) == 1
    assert "台積電最近營收如何？" in restated[0]  # the earlier turn is what resolves "資本支出呢"
    assert "那資本支出呢？" in restated[0]
    assert "台積電第四季資本支出是多少？" in stub.asked("plan research")[-1]


_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5E"
    "rkJggg=="
)


def test_an_attached_image_is_described_and_used_as_context_only(
    research_client: TestClient, settings: Settings, stub: _ResearchClient
) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    resp = research_client.post(
        "/api/v1/agent/query",
        json={
            "message": "這張圖是什麼？",
            "deep_research": True,
            "attachments": [
                {"filename": "chart.png", "media_type": "image/png", "data_base64": _PNG}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert len(stub.asked("Describe what the attached image")) == 1
    assert "candlestick chart of 2330" in stub.asked("plan research")[0]
    # the description guides the research; it is not a source the report can cite
    frames = _parse_sse(resp.text)
    sources = next(f for f in frames if f["event"] == "web_search.result")["data"]["sources"]
    assert [s["url"] for s in sources] == [_PAGE]
