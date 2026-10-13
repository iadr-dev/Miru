"""The source ledger: every source a research run may cite, each with a stable id.

The ledger is what makes citations checkable. A *web* source only gets an id if its URL survives
canonicalisation (public http/https, no credentials, no internal hosts), so a model-invented or
hostile URL can neither be fetched nor cited; a *data* source is Miru's own market data, added by
the run itself with the text already in hand. The report may cite only ids that exist here.

Several sub-questions work against one ledger at the same time. Nothing in it awaits, so each method
is atomic with respect to the event loop; `claim_unread` is what stops two workers fetching the same
page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from research.ports import FetchedPage, SearchHit
from schemas.urlsafety import canonical_url


@dataclass
class Source:
    id: int
    kind: Literal["web", "data"]
    url: str | None
    title: str
    snippet: str = ""
    published: str | None = None
    text: str = ""
    read_attempted: bool = False
    claimed: bool = False  # a worker has taken it to read; nobody else should

    @property
    def citeable(self) -> bool:
        """Only a source whose text was actually read can back a claim."""
        return bool(self.text)


class SourceLedger:
    def __init__(self, *, max_sources: int) -> None:
        self._max = max_sources  # web hits kept; data sources are bounded by the data-call budget
        self._by_id: dict[int, Source] = {}
        self._by_url: dict[str, int] = {}
        self._next_id = 1

    def offer(self, hit: SearchHit) -> Source | None:
        """Register a search hit; returns the new source, or None if it was invalid, a duplicate,
        or the ledger is full."""
        url = canonical_url(hit.url)
        if url is None or url in self._by_url or len(self._by_url) >= self._max:
            return None
        source = Source(
            id=self._next_id,
            kind="web",
            url=url,
            title=hit.title.strip() or url,
            snippet=hit.snippet.strip(),
            published=hit.published,
        )
        self._next_id += 1
        self._by_id[source.id] = source
        self._by_url[url] = source.id
        return source

    def add_data(self, *, title: str, text: str) -> Source | None:
        """Register one market-data result as a source that is already read. Returns None when
        there is no text to cite."""
        if not text.strip():
            return None
        source = Source(
            id=self._next_id,
            kind="data",
            url=None,
            title=title.strip(),
            text=text,
            read_attempted=True,
            claimed=True,
        )
        self._next_id += 1
        self._by_id[source.id] = source
        return source

    def claim_unread(self, limit: int) -> list[Source]:
        """Take up to `limit` web sources nobody has read or claimed yet, marking them claimed."""
        taken: list[Source] = []
        for source in self._by_id.values():
            if len(taken) >= limit:
                break
            if source.kind == "web" and not source.read_attempted and not source.claimed:
                source.claimed = True
                taken.append(source)
        return taken

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
        if source is not None and source.url is not None:
            self._by_url.pop(source.url, None)

    def citeable(self) -> list[Source]:
        return [s for s in self._by_id.values() if s.citeable]

    def get(self, source_id: int) -> Source | None:
        return self._by_id.get(source_id)

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = ["Source", "SourceLedger", "canonical_url"]
