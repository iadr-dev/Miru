from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.news_tools import GetNewsFeedInput, GetNewsFeedTool


class _FakeNewsGateway:
    """No network — records calls, returns a canned `SourceResult`."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_news(
        self, *, query: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        self.calls.append(query)
        return SourceResult(
            source_id="NEWS",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="caveat",
            payload={
                "items": [
                    {
                        "title": "台積電法說會",
                        "link": "https://news.cnyes.com/fake",
                        "source": "cnyes_tw_stock",
                        "published_at": datetime.now(UTC).isoformat(),
                        "summary": None,
                    }
                ]
            },
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


async def test_get_news_feed_delegates_and_parses_items() -> None:
    gateway = _FakeNewsGateway()
    tool = GetNewsFeedTool(gateway)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(GetNewsFeedInput(query="2330"), ToolContext())

    assert gateway.calls == ["2330"]
    assert output.currentness_caveat == "caveat"
    assert output.items[0].title == "台積電法說會"
    assert output.items[0].source == "cnyes_tw_stock"


async def test_get_news_feed_defaults_query() -> None:
    gateway = _FakeNewsGateway()
    tool = GetNewsFeedTool(gateway)

    await tool.run(GetNewsFeedInput(), ToolContext())

    assert gateway.calls == ["台股"]


async def test_get_news_feed_handles_no_items() -> None:
    class _EmptyGateway:
        async def fetch_news(
            self, *, query: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
        ) -> SourceResult:
            return SourceResult(
                source_id="NEWS",
                data_class=DataClass.PUBLIC,
                timing_class=TimingClass.REFERENCE,
                fetched_at=datetime.now(UTC),
                currentness_caveat="caveat",
                payload={},
                health=SourceHealth(status=HealthStatus.STALE, detail="no items"),
            )

    tool = GetNewsFeedTool(_EmptyGateway())

    output = await tool.run(GetNewsFeedInput(), ToolContext())

    assert output.items == ()
