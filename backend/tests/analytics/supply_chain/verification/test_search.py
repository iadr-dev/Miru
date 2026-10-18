from __future__ import annotations

from types import SimpleNamespace

from analytics.supply_chain.verification.search import BraveSearchProvider
from schemas.source import HealthStatus


class FakeGateway:
    def __init__(self, status: HealthStatus, payload: dict[str, object]) -> None:
        self.status, self.payload = status, payload

    def _result(self) -> SimpleNamespace:
        return SimpleNamespace(payload=self.payload, health=SimpleNamespace(status=self.status))

    async def search(self, query: str, **_: object) -> SimpleNamespace:
        return self._result()

    async def fetch_page(self, url: str, **_: object) -> SimpleNamespace:
        return self._result()


def provider(status: HealthStatus, payload: dict[str, object]) -> BraveSearchProvider:
    return BraveSearchProvider(FakeGateway(status, payload))  # type: ignore[arg-type]


async def test_hits_are_parsed_and_non_http_results_dropped() -> None:
    payload: dict[str, object] = {
        "hits": [
            {"title": "t", "url": "https://a.example.com/x", "snippet": "s", "published": "2d"},
            {"title": "t", "url": "javascript:alert(1)", "snippet": "s"},
            "junk",
        ]
    }
    hits = await provider(HealthStatus.OK, payload).search("q", count=5)
    assert hits is not None and [h.url for h in hits] == ["https://a.example.com/x"]
    assert hits[0].published == "2d"


async def test_unavailable_provider_is_none_and_no_results_is_an_empty_list() -> None:
    assert await provider(HealthStatus.UNAVAILABLE_TIER, {}).search("q", count=5) is None
    assert await provider(HealthStatus.UNREACHABLE, {}).search("q", count=5) is None
    assert await provider(HealthStatus.STALE, {"hits": []}).search("q", count=5) == []


async def test_fetch_returns_text_or_none() -> None:
    page = {"url": "https://a.example.com/x", "title": "T", "text": "body"}
    ok = await provider(HealthStatus.OK, page).fetch("https://a.example.com/x")
    assert ok is not None and ok.text == "body" and ok.title == "T"
    assert await provider(HealthStatus.UNREACHABLE, {}).fetch("https://a.example.com/x") is None
