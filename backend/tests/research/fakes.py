"""Scripted stand-ins for the model and the web, so the research loop runs with no network."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import cast

from pydantic import BaseModel

from research.ports import FetchedPage, SearchHit


class ScriptedModel:
    """Returns queued answers per output schema, in order, and records every prompt it was given.
    The last answer for a schema repeats, so an open-ended loop keeps getting the same reply."""

    def __init__(self, script: dict[type[BaseModel], list[BaseModel | Exception]]) -> None:
        self._script = {schema: list(answers) for schema, answers in script.items()}
        self.calls: list[tuple[str, str, str]] = []  # (schema name, system, user)

    async def generate[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((schema.__name__, system, user))
        answers = self._script[schema]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return cast("T", answer)

    def prompts(self, schema: type[BaseModel]) -> list[str]:
        return [user for name, _, user in self.calls if name == schema.__name__]


class FakeWeb:
    """`results` maps a query to its hits; `pages` maps a URL to its text (None = unreadable)."""

    def __init__(
        self,
        results: dict[str, list[SearchHit]],
        pages: dict[str, str | None],
        *,
        fail_search: set[str] | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self._results = results
        self._pages = pages
        self._fail_search = fail_search or set()
        self._on_call = on_call
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []

    async def search(self, query: str, *, count: int) -> list[SearchHit]:
        self.search_calls.append(query)
        if self._on_call:
            self._on_call()
        if query in self._fail_search:
            raise RuntimeError("search backend down")
        return self._results.get(query, [])[:count]

    async def fetch(self, url: str) -> FetchedPage | None:
        self.fetch_calls.append(url)
        if self._on_call:
            self._on_call()
        text = self._pages.get(url)
        if text is None:
            return None
        return FetchedPage(url=url, title=f"Title of {url}", text=text)


def hit(n: int, *, host: str = "news.example") -> SearchHit:
    return SearchHit(
        title=f"Result {n}",
        url=f"https://{host}/article-{n}",
        snippet=f"snippet {n}",
        published="2026-10-01",
    )


def pages_for(
    hits: list[SearchHit], text: str = "Revenue grew 12% year on year."
) -> dict[str, str | None]:
    return {h.url: f"{text} (page for {h.title})" for h in hits}


class Recorder:
    """Collects progress events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, int]] = []

    async def __call__(self, progress: object) -> None:
        stage = getattr(progress, "stage", "")
        self.events.append((stage, getattr(progress, "detail", ""), getattr(progress, "round", 0)))

    def stages(self) -> list[str]:
        return [stage for stage, _, _ in self.events]


def by_stage(events: list[tuple[str, str, int]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for stage, _, _ in events:
        counts[stage] += 1
    return dict(counts)
