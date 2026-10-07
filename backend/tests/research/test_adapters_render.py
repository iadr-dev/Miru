"""The real-world edges of deep research: model JSON parsing, gateway mapping, report markdown."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel

from dataaccess.web_search import WebSearchGateway
from llm.base import LLMResponse, Message, StopReason, TokenUsage
from research.adapters import GatewayWeb, LLMResearchModel, governance_guard
from research.models import FinalReport, ReportClaim, ReportSection, ReportSource
from research.render import render_markdown
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.web_search import WebSearchAdapter

pytestmark = pytest.mark.governance


class Answer(BaseModel):
    ok: bool


class StubClient:
    provider_id = "stub"
    model_id = "stub"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], *_a: object, **_k: object) -> LLMResponse:
        self.calls.append(messages)
        text = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return LLMResponse(
            text=text,
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


async def test_json_is_pulled_out_of_a_fenced_reply_and_usage_is_reported() -> None:
    client = StubClient(['Sure!\n```json\n{"ok": true}\n```'])
    used: list[tuple[int, int]] = []
    model = LLMResearchModel(client, on_usage=lambda i, o: used.append((i, o)))  # type: ignore[arg-type]
    assert (await model.generate(system="s", user="u", schema=Answer)).ok is True
    assert used == [(10, 5)]
    assert "ONE JSON object" in (client.calls[0][0].content or "")


async def test_one_retry_on_invalid_output_then_give_up() -> None:
    fixed = LLMResearchModel(StubClient(["no json here", '{"ok": false}']))  # type: ignore[arg-type]
    assert (await fixed.generate(system="s", user="u", schema=Answer)).ok is False
    broken = LLMResearchModel(StubClient(["nope", '{"wrong": 1}']))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Answer"):
        await broken.generate(system="s", user="u", schema=Answer)


def _source_result(status: HealthStatus, payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="WEB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="test",
        payload=payload,
        health=SourceHealth(status=status, detail="t"),
    )


class _StubAdapter(WebSearchAdapter):
    def __init__(self, status: HealthStatus) -> None:
        self._status = status

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        hit = {"title": "T", "url": "https://a.example/x", "snippet": "S", "published": "1 day"}
        return _source_result(self._status, {"hits": [hit, "junk"]})

    async def fetch_page(
        self, url: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, **_kwargs: object
    ) -> SourceResult:  # type: ignore[override]
        payload = {"url": url, "title": "Page", "text": "body"}
        return _source_result(self._status, payload)


async def test_the_gateway_port_maps_hits_and_pages() -> None:
    web = GatewayWeb(WebSearchGateway(_StubAdapter(HealthStatus.OK)), tls_policy=TLSPolicy.STRICT)
    hits = await web.search("q", count=5)
    assert [(h.title, h.url, h.published) for h in hits] == [("T", "https://a.example/x", "1 day")]
    page = await web.fetch("https://a.example/x")
    assert page is not None
    assert page.text == "body"
    assert page.title == "Page"


async def test_an_unhealthy_source_yields_nothing() -> None:
    web = GatewayWeb(
        WebSearchGateway(_StubAdapter(HealthStatus.UNREACHABLE)), tls_policy=TLSPolicy.STRICT
    )
    assert await web.search("q", count=5) == []
    assert await web.fetch("https://a.example/x") is None


def test_the_guard_redacts_trading_language() -> None:
    assert "目標價" not in governance_guard("分析師給出目標價 1200 元。")
    assert governance_guard("Revenue rose 12%.") == "Revenue rose 12%."


def _report(**overrides: object) -> FinalReport:
    detail = ReportSection(
        heading="Detail", claims=(ReportClaim(text="HPC led.", source_ids=(2,)),)
    )
    base: dict[str, object] = {
        "question": "q",
        "title": "TSMC revenue",
        "as_of": date(2026, 10, 6),
        "summary": (ReportClaim(text="Revenue rose 12%.", source_ids=(1, 2)),),
        "sections": (detail,),
        "open_questions": ("Guidance is unclear.",),
        "sources": (
            ReportSource(id=1, title="A", url="https://a.example/1"),
            ReportSource(id=2, title="B", url="https://b.example/2"),
        ),
        "stopped_because": "sufficient",
        "rounds": 2,
        "searches": 3,
        "pages_read": 4,
    }
    return FinalReport.model_validate(base | overrides)


def test_markdown_has_citations_sources_and_the_caveat() -> None:
    md = render_markdown(_report(), "en")
    assert "- Revenue rose 12%. [1][2]" in md
    assert "1. [A](https://a.example/1)" in md
    assert "As of 2026-10-06" in md
    assert "recommendation" in md


def test_an_early_stop_is_disclosed_and_empty_reports_say_so() -> None:
    assert "limit" in render_markdown(_report(stopped_because="time"), "en")
    assert "上限" in render_markdown(_report(stopped_because="rounds"), "zh-TW")
    empty = render_markdown(_report(summary=(), sections=()), "zh-TW")
    assert "無法產出" in empty
    assert "### 來源" not in empty


def test_page_derived_text_cannot_form_links_or_images() -> None:
    hostile = ReportClaim(text="![x](https://evil.example/?q=1) <b>hi</b>", source_ids=(1,))
    md = render_markdown(_report(summary=(hostile,), title="[t](https://evil.example)"), "en")
    assert "![x](" not in md
    assert "[t](https://evil.example)" not in md
    assert "<b>" not in md


def test_a_trailing_backslash_cannot_cancel_the_escape() -> None:
    hostile = ReportClaim(text="x \\[t](https://evil.example)", source_ids=(1,))
    md = render_markdown(_report(summary=(hostile,)), "en")
    assert "\\\\\\[t\\]" in md


def test_headings_and_open_questions_are_escaped_too() -> None:
    section = ReportSection(
        heading="![x](https://evil.example/a.png)",
        claims=(ReportClaim(text="ok", source_ids=(1,)),),
    )
    md = render_markdown(
        _report(sections=(section,), open_questions=("[click](https://evil.example)",)), "en"
    )
    assert "![x](" not in md
    assert "[click](https://evil.example)" not in md
