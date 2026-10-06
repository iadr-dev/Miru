"""The two things deep research needs from the outside world, as narrow ports.

The research loop never talks to a provider SDK or an HTTP client directly: it asks a
`ResearchModel` for structured output and a `WebPort` for search hits and page text. The real
adapters (an `LLMClient`-backed model, the `WebSearchGateway`) are wired at the composition root;
tests supply scripted fakes, so the whole loop runs with no network.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    snippet: str = ""
    published: str | None = None


class FetchedPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    title: str = ""
    text: str


class WebPort(Protocol):
    """Web access. Everything it returns is untrusted external content."""

    async def search(self, query: str, *, count: int) -> list[SearchHit]: ...

    async def fetch(self, url: str) -> FetchedPage | None:
        """The page's readable text (size-capped by the adapter), or None if it can't be read."""
        ...


class ResearchModel(Protocol):
    async def generate[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        """One model call that must return `schema`. Raises if the output isn't valid."""
        ...


__all__ = ["FetchedPage", "ResearchModel", "SearchHit", "WebPort"]
