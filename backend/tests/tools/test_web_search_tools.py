from __future__ import annotations

from datetime import UTC, datetime

import pytest

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.web_search_tools import (
    _FETCH_URL_TEXT_CAP,
    FetchUrlInput,
    FetchUrlTool,
    WebSearchInput,
    WebSearchTool,
)


class _FakeGateway:
    """No network — records calls, returns a canned `SourceResult`."""

    def __init__(self, status: HealthStatus = HealthStatus.OK) -> None:
        self.search_calls: list[tuple[str, int]] = []
        self.fetch_calls: list[str] = []
        self._status = status

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        self.search_calls.append((query, count))
        return SourceResult(
            source_id="WEB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="caveat",
            payload={
                "hits": [
                    {
                        "title": "A result",
                        "url": "https://example.com",
                        "snippet": "snippet",
                        "published": None,
                    }
                ]
                if self._status is HealthStatus.OK
                else []
            },
            health=SourceHealth(status=self._status, detail="x"),
        )

    async def fetch_url(
        self, url: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        self.fetch_calls.append(url)
        return SourceResult(
            source_id="WEB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="caveat",
            payload={"url": url, "text": "x" * (_FETCH_URL_TEXT_CAP + 100)},
            health=SourceHealth(status=self._status, detail="x"),
        )


async def test_web_search_delegates_and_parses_hits() -> None:
    gateway = _FakeGateway()
    tool = WebSearchTool(gateway)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(WebSearchInput(query="台積電"), ToolContext())

    assert gateway.search_calls == [("台積電", 10)]
    assert output.hits[0].title == "A result"
    assert output.health_status is HealthStatus.OK
    assert output.source_id == "WEB"


@pytest.mark.parametrize(
    "status", [HealthStatus.STALE, HealthStatus.UNREACHABLE, HealthStatus.UNAVAILABLE_TIER]
)
async def test_web_search_and_fetch_url_report_health_on_an_empty_result(
    status: HealthStatus,
) -> None:
    gateway = _FakeGateway(status)

    searched = await WebSearchTool(gateway).run(WebSearchInput(query="q"), ToolContext())
    fetched = await FetchUrlTool(gateway).run(
        FetchUrlInput(url="https://example.com"), ToolContext()
    )

    assert searched.hits == ()
    assert searched.health_status is status
    assert fetched.health_status is status


async def test_fetch_url_caps_text_length() -> None:
    gateway = _FakeGateway()
    tool = FetchUrlTool(gateway)

    output = await tool.run(FetchUrlInput(url="https://example.com"), ToolContext())

    assert gateway.fetch_calls == ["https://example.com"]
    assert output.truncated is True
    assert output.text is not None
    assert len(output.text) == _FETCH_URL_TEXT_CAP
