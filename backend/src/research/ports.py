"""What deep research needs from the outside world, as narrow ports.

The research loop never talks to a provider SDK, an HTTP client or the tool registry directly: it
asks a `ResearchModel` for structured output (and, for attached images, a description), a `WebPort`
for search hits and page text, and a `DataPort` for Miru's own market data. The real adapters are
wired at the composition root; tests supply scripted fakes, so the whole loop runs with no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from llm.base import ImagePart
from research.models import DataRequest


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


class DataResult(BaseModel):
    """One market-data lookup rendered as text the model can read and a report can cite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    text: str


class WebPort(Protocol):
    """Web access. Everything it returns is untrusted external content."""

    async def search(self, query: str, *, count: int) -> list[SearchHit]: ...

    async def fetch(self, url: str) -> FetchedPage | None:
        """The page's readable text (size-capped by the adapter), or None if it can't be read."""
        ...


class DataPort(Protocol):
    """Miru's own market data, by the fixed requests in `research.datatools`."""

    async def fetch(self, request: DataRequest) -> DataResult | None:
        """The result, or None when there is nothing to show (no data, or locked for this tier)."""
        ...


class ResearchModel(Protocol):
    async def generate[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        """One model call that must return `schema`. Raises if the output isn't valid."""
        ...

    async def describe_images(self, *, images: Sequence[ImagePart], question: str) -> str:
        """What the attached images show that bears on `question`. Raises if the model can't."""
        ...


__all__ = ["DataPort", "DataResult", "FetchedPage", "ResearchModel", "SearchHit", "WebPort"]
