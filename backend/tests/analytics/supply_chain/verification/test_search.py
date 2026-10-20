from __future__ import annotations

from types import SimpleNamespace

import pytest

from analytics.supply_chain.verification import search as search_module
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


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(search_module.asyncio, "sleep", fake_sleep)
    return waited


class SequenceGateway(FakeGateway):
    def __init__(self, statuses: list[HealthStatus], payload: dict[str, object]) -> None:
        super().__init__(statuses[0], payload)
        self._statuses = list(statuses)
        self.calls = 0

    async def search(self, query: str, **_: object) -> SimpleNamespace:
        self.status = self._statuses[min(self.calls, len(self._statuses) - 1)]
        self.calls += 1
        return self._result()


async def test_a_refused_search_is_retried_once_after_a_pause(sleeps: list[float]) -> None:
    payload: dict[str, object] = {"hits": [{"title": "t", "url": "https://a.example.com/x"}]}
    gateway = SequenceGateway([HealthStatus.UNAVAILABLE_TIER, HealthStatus.OK], payload)
    hits = await BraveSearchProvider(gateway).search("q", count=3)  # type: ignore[arg-type]
    assert hits is not None and len(hits) == 1
    assert gateway.calls == 2 and search_module._RETRY_WAIT_SECONDS in sleeps


async def test_a_search_that_stays_refused_gives_up_after_one_retry(sleeps: list[float]) -> None:
    gateway = SequenceGateway([HealthStatus.UNAVAILABLE_TIER], {})
    assert await BraveSearchProvider(gateway).search("q", count=3) is None  # type: ignore[arg-type]
    assert gateway.calls == 2


async def test_back_to_back_searches_are_spaced_out(sleeps: list[float]) -> None:
    payload: dict[str, object] = {"hits": []}
    gateway = SequenceGateway([HealthStatus.OK], payload)
    brave = BraveSearchProvider(gateway)  # type: ignore[arg-type]
    await brave.search("a", count=3)
    await brave.search("b", count=3)
    assert any(0 < wait <= search_module._MIN_GAP_SECONDS for wait in sleeps)
