"""The source ledger: every page a research run may cite, each with a stable id.

The ledger is what makes citations checkable. A source only gets an id if its URL survives
canonicalisation (public http/https, no credentials, no internal hosts), so a model-invented or
hostile URL can neither be fetched nor cited; and the report may cite only ids that exist here.
"""

from __future__ import annotations

from dataclasses import dataclass

from research.ports import FetchedPage, SearchHit
from schemas.urlsafety import canonical_url


@dataclass
class Source:
    id: int
    url: str
    title: str
    snippet: str
    published: str | None
    text: str = ""
    read_attempted: bool = False

    @property
    def citeable(self) -> bool:
        """Only a page whose text was actually read can back a claim."""
        return bool(self.text)


class SourceLedger:
    def __init__(self, *, max_sources: int) -> None:
        self._max = max_sources
        self._by_id: dict[int, Source] = {}
        self._by_url: dict[str, int] = {}
        self._next_id = 1

    def offer(self, hit: SearchHit) -> Source | None:
        """Register a search hit; returns the new source, or None if it was invalid, a duplicate,
        or the ledger is full."""
        url = canonical_url(hit.url)
        if url is None or url in self._by_url or len(self._by_id) >= self._max:
            return None
        source = Source(
            id=self._next_id,
            url=url,
            title=hit.title.strip() or url,
            snippet=hit.snippet.strip(),
            published=hit.published,
        )
        self._next_id += 1
        self._by_id[source.id] = source
        self._by_url[url] = source.id
        return source

    def attach(self, source_id: int, page: FetchedPage, *, chars: int) -> None:
        source = self._by_id.get(source_id)
        if source is None:
            return
        source.read_attempted = True
        source.text = page.text[:chars]
        if page.title.strip():
            source.title = page.title.strip()

    def mark_unreadable(self, source_id: int) -> None:
        source = self._by_id.get(source_id)
        if source is not None:
            source.read_attempted = True

    def exclude(self, source_id: int) -> None:
        """Remove a source entirely (e.g. a page that tried to instruct the model)."""
        source = self._by_id.pop(source_id, None)
        if source is not None:
            self._by_url.pop(source.url, None)

    def unread(self) -> list[Source]:
        return [s for s in self._by_id.values() if not s.read_attempted]

    def citeable(self) -> list[Source]:
        return [s for s in self._by_id.values() if s.citeable]

    def get(self, source_id: int) -> Source | None:
        return self._by_id.get(source_id)

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = ["Source", "SourceLedger", "canonical_url"]
