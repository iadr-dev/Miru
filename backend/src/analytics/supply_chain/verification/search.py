"""Finding and reading pages. No network code here: the only network path is the injected
`SearchProvider`, and the Brave-backed one goes through `dataaccess.web_search`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol, cast

import structlog
from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.models import Kind, Trust
from analytics.supply_chain.domain.rules import trust_for_url
from analytics.supply_chain.verification.budget import Budget
from analytics.supply_chain.verification.models import Company, Page, Target
from dataaccess.web_search import WebSearchGateway
from schemas.enums import TLSPolicy
from schemas.source import HealthStatus

logger = structlog.get_logger(__name__)

_SEARCH_COUNT: Final = 10
_TRUST_RANK: Final = {Trust.PRIMARY: 0, Trust.SECONDARY: 1, Trust.TERTIARY: 2, Trust.REJECTED: 3}


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = ""
    url: str
    snippet: str = ""
    published: str | None = None


class SearchProvider(Protocol):
    async def search(self, query: str, *, count: int) -> list[SearchHit] | None:
        """Hits, or None when the provider is unavailable (no key, transport error)."""
        ...

    async def fetch(self, url: str) -> Page | None:
        """The page's readable text, or None when it could not be read."""
        ...


class BraveSearchProvider:
    """`SearchProvider` over the existing web-search gateway (Brave search, guarded page reads)."""

    def __init__(
        self, gateway: WebSearchGateway | None = None, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> None:
        self._gateway = gateway or WebSearchGateway()
        self._tls = tls_policy

    async def search(self, query: str, *, count: int = _SEARCH_COUNT) -> list[SearchHit] | None:
        result = await self._gateway.search(query, tls_policy=self._tls, count=count)
        if result.health.status in (HealthStatus.UNAVAILABLE_TIER, HealthStatus.UNREACHABLE):
            return None
        raw = result.payload.get("hits")
        hits: list[SearchHit] = []
        for item in cast("list[object]", raw) if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            fields = cast("dict[str, object]", item)
            url = str(fields.get("url", ""))
            if not url.startswith(("http://", "https://")):
                continue
            published = fields.get("published")
            hits.append(
                SearchHit(
                    title=str(fields.get("title", "")),
                    url=url,
                    snippet=str(fields.get("snippet", "")),
                    published=published if isinstance(published, str) else None,
                )
            )
        return hits

    async def fetch(self, url: str) -> Page | None:
        result = await self._gateway.fetch_page(url, tls_policy=self._tls)
        text = result.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        title = result.payload.get("title")
        return Page(url=str(result.payload.get("url") or url), title=str(title or ""), text=text)


def queries_for(target: Target) -> list[str]:
    """Up to two queries: Traditional Chinese names, then English names."""
    supplier = target.supplier
    customer = target.customer
    if customer is None:
        zh = supplier.zh_name or supplier.name
        queries = [f"{zh} 主要客戶 供應商"]
        if supplier.en_name:
            queries.append(f"{supplier.en_name} major customers suppliers")
        return queries
    relation_zh = "競爭對手" if target.kind is Kind.COMPETES else "供應 客戶"
    relation_en = "competitor" if target.kind is Kind.COMPETES else "supplier customer"
    queries = [f"{_zh(supplier)} {_zh(customer)} {relation_zh}"]
    if supplier.en_name or customer.en_name:
        queries.append(f"{_en(supplier)} {_en(customer)} {relation_en}")
    return queries


def _zh(company: Company) -> str:
    return company.zh_name or company.name


def _en(company: Company) -> str:
    return company.en_name or company.name


def pick_hits(
    hits: Sequence[SearchHit],
    *,
    limit: int,
    trust_table: Mapping[str, Trust] | None = None,
) -> list[SearchHit]:
    """Drop rejected publishers and duplicates, then prefer primary, secondary, tertiary (the
    search order is kept within a tier)."""
    seen: set[str] = set()
    usable: list[tuple[int, int, SearchHit]] = []
    for order, hit in enumerate(hits):
        trust = trust_for_url(hit.url, trust_table)
        if trust is Trust.REJECTED or hit.url in seen:
            continue
        seen.add(hit.url)
        usable.append((_TRUST_RANK[trust], order, hit))
    usable.sort(key=lambda row: (row[0], row[1]))
    return [hit for _, _, hit in usable[:limit]]


async def find_hits(
    provider: SearchProvider,
    target: Target,
    budget: Budget,
    *,
    pages_per_claim: int,
    trust_table: Mapping[str, Trust] | None = None,
) -> list[SearchHit] | None:
    """Search (within the budget) and choose the pages to read. None = the provider is down."""
    collected: list[SearchHit] = []
    for query in queries_for(target):
        if not budget.try_search():
            break
        found = await provider.search(query, count=_SEARCH_COUNT)
        if found is None:
            return None
        collected.extend(found)
    return pick_hits(collected, limit=pages_per_claim, trust_table=trust_table)


__all__ = [
    "BraveSearchProvider",
    "SearchHit",
    "SearchProvider",
    "find_hits",
    "pick_hits",
    "queries_for",
]
