"""Scripted stand-ins for the model, the web and the market data, so the research loop runs with no
network."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import cast

from pydantic import BaseModel

from llm.base import ImagePart
from research.controller import Progress
from research.models import DataRequest
from research.ports import DataResult, FetchedPage, SearchHit


class ScriptedModel:
    """Returns queued answers per output schema, in order, and records every prompt it was given.
    The last answer for a schema repeats, so an open-ended loop keeps getting the same reply.
    `image_notes` is what it says about attached images (or an exception to raise)."""

    def __init__(
        self,
        script: dict[type[BaseModel], list[BaseModel | Exception]],
        *,
        image_notes: str | Exception = "",
    ) -> None:
        self._script = {schema: list(answers) for schema, answers in script.items()}
        self._image_notes = image_notes
        self.calls: list[tuple[str, str, str]] = []  # (schema name, system, user)
        self.image_calls: list[tuple[int, str]] = []  # (how many images, the question)

    async def generate[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((schema.__name__, system, user))
        answers = self._script[schema]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return cast("T", answer)

    async def describe_images(self, *, images: Sequence[ImagePart], question: str) -> str:
        self.image_calls.append((len(images), question))
        if isinstance(self._image_notes, Exception):
            raise self._image_notes
        return self._image_notes

    def prompts(self, schema: type[BaseModel]) -> list[str]:
        return [user for name, _, user in self.calls if name == schema.__name__]


class FakeWeb:
    """`results` maps a query to its hits; `pages` maps a URL to its text (None = unreadable).
    `delay` makes every call take that long, so side-by-side work overlaps and can be observed."""

    def __init__(
        self,
        results: dict[str, list[SearchHit]],
        pages: dict[str, str | None],
        *,
        fail_search: set[str] | None = None,
        on_call: Callable[[], None] | None = None,
        delay: float = 0.0,
    ) -> None:
        self._results = results
        self._pages = pages
        self._fail_search = fail_search or set()
        self._on_call = on_call
        self._delay = delay
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self._searching = 0
        self.max_concurrent_searches = 0

    async def search(self, query: str, *, count: int) -> list[SearchHit]:
        self.search_calls.append(query)
        if self._on_call:
            self._on_call()
        self._searching += 1
        self.max_concurrent_searches = max(self.max_concurrent_searches, self._searching)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if query in self._fail_search:
                raise RuntimeError("search backend down")
            return self._results.get(query, [])[:count]
        finally:
            self._searching -= 1

    async def fetch(self, url: str) -> FetchedPage | None:
        self.fetch_calls.append(url)
        if self._on_call:
            self._on_call()
        if self._delay:
            await asyncio.sleep(self._delay)
        text = self._pages.get(url)
        if text is None:
            return None
        return FetchedPage(url=url, title=f"Title of {url}", text=text)


class FakeData:
    """`results` maps (tool, symbol) to what that lookup returns (None = nothing to show);
    a key in `fail` raises instead."""

    def __init__(
        self,
        results: dict[tuple[str, str], DataResult | None] | None = None,
        *,
        fail: set[tuple[str, str]] | None = None,
    ) -> None:
        self._results = results or {}
        self._fail = fail or set()
        self.calls: list[DataRequest] = []

    async def fetch(self, request: DataRequest) -> DataResult | None:
        self.calls.append(request)
        key = (request.tool, request.symbol)
        if key in self._fail:
            raise RuntimeError("data backend down")
        return self._results.get(key)


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
        self.progress: list[Progress] = []

    async def __call__(self, progress: Progress) -> None:
        self.progress.append(progress)

    @property
    def events(self) -> list[tuple[str, str, int]]:
        return [(p.stage, p.detail, p.round) for p in self.progress]

    def stages(self) -> list[str]:
        return [p.stage for p in self.progress]

    def lanes(self, stage: str) -> list[int]:
        return [p.lane for p in self.progress if p.stage == stage]


def by_stage(events: list[tuple[str, str, int]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for stage, _, _ in events:
        counts[stage] += 1
    return dict(counts)
