"""Deep research end to end over HTTP: the Pro gate, the daily limit and its refund, the streamed
progress lanes, and a cited, governance-scanned report - with a scripted model and a fake web, so
no provider or network is touched."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api import deep_research_turn
from config.settings import Settings
from dataaccess.web_search import WebSearchGateway
from llm.base import LLMResponse, Message, ModelCapabilities, StopReason, TokenUsage
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

    async def chat(
        self, messages: list[Message], tools: object = None, *, native_web_search: bool = False
    ) -> LLMResponse:
        del tools, native_web_search
        system = messages[0].content or ""
        if "plan web research" in system:
            body = {"sub_questions": ["revenue"], "queries": ["tsmc revenue"]}
        elif "review research progress" in system:
            body = {"sufficient": True}
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
def research_client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(deep_research_turn, "build_gateway", _FakeGateway)
    with build_test_client(settings, agent_answer="x", agent_client=_ResearchClient()) as client:
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
    assert "搜尋：tsmc revenue" in activities
    assert activities[-1] == "撰寫研究報告"
    assert sum(f["event"] == "agent.done" for f in frames) == len(activities)

    sources = next(f for f in frames if f["event"] == "web_search.result")["data"]["sources"]
    assert sources == [{"url": _PAGE, "title": "TSMC monthly revenue"}]

    text = next(f for f in frames if f["event"] == "message")["data"]["text"]
    assert "September revenue rose 12% year on year. [1]" in text
    assert f"1. [TSMC monthly revenue]({_PAGE})" in text
    assert "recommendation" in text or "建議" in text  # the standard non-trading caveat
    assert frames[-1]["data"]["status"] == "done"


def test_the_daily_limit_is_five_reports(research_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(research_client, settings, tier=Tier.PRO)
    for _ in range(5):
        assert _ask(research_client, headers).status_code == 200
    blocked = _ask(research_client, headers)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "deep_research_limit_reached"
    # ordinary chat is unaffected by the report limit
    assert _ask(research_client, headers, deep=False).status_code == 200


def test_a_run_that_finds_nothing_is_refunded(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deep_research_turn, "build_gateway", lambda: _FakeGateway(has_results=False)
    )
    with build_test_client(settings, agent_answer="x", agent_client=_ResearchClient()) as client:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        for _ in range(7):  # more than the daily limit: none of these produced a report
            resp = _ask(client, headers)
            assert resp.status_code == 200
            text = next(f for f in _parse_sse(resp.text) if f["event"] == "message")["data"]["text"]
            assert "無法產出" in text


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
