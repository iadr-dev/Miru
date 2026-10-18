"""Synthetic fixtures for the research pipeline: invented companies, example.com pages."""

from __future__ import annotations

import json
from datetime import date

from analytics.supply_chain.domain.models import Kind
from analytics.supply_chain.verification.models import (
    Company,
    Judgement,
    Page,
    Target,
    TargetSource,
    claim_id_for,
)
from analytics.supply_chain.verification.search import SearchHit
from tests.llm.stubs import StubLLMClient, make_text_response

TODAY = date(2026, 10, 18)

ALPHA = Company(
    symbol="9001",
    market="TW",
    names=("阿爾法零件", "Alpha Parts Co., Ltd."),
    market_value=500.0,
)
BETA = Company(
    symbol="9002",
    market="TW",
    names=("貝塔裝置", "Beta Devices Inc."),
    market_value=900.0,
)
GAMMA = Company(symbol="ZZZA", market="US", names=("Gamma Corp",), market_value=0.0)

SENTENCE = "阿爾法零件目前持續供貨給貝塔裝置，為其主要供應商之一。"
PAGE_TEXT = f"產業新聞 2026-09-01\n\n{SENTENCE}\n其他無關內容。"


def target(
    supplier: Company = ALPHA, customer: Company = BETA, kind: Kind = Kind.SUPPLIES
) -> Target:
    return Target(
        claim_id=claim_id_for(supplier, customer, kind),
        source=TargetSource.CANDIDATES,
        supplier=supplier,
        customer=customer,
        kind=kind,
        priority=max(supplier.market_value, customer.market_value),
    )


def judgement_json(**overrides: object) -> str:
    base: dict[str, object] = {
        "supports": True,
        "direction": "supplies",
        "supplier_is_first": True,
        "excerpt": SENTENCE,
        "current": True,
        "anonymous_counterparty": False,
        "as_of_year": 2026,
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def judgement(**overrides: object) -> Judgement:
    return Judgement.model_validate(json.loads(judgement_json(**overrides)))


def llm(*replies: str) -> StubLLMClient:
    return StubLLMClient(script=[make_text_response(r) for r in replies])


def page(url: str = "https://news.example.com/a", text: str = PAGE_TEXT) -> Page:
    return Page(url=url, title="t", text=text)


class StubSearch:
    """A `SearchProvider` with canned hits and pages; records every call."""

    def __init__(
        self,
        hits: list[SearchHit] | None,
        pages: dict[str, str] | None = None,
    ) -> None:
        self.hits = hits
        self.pages = pages or {}
        self.queries: list[str] = []
        self.fetched: list[str] = []

    async def search(self, query: str, *, count: int) -> list[SearchHit] | None:
        self.queries.append(query)
        return self.hits

    async def fetch(self, url: str) -> Page | None:
        self.fetched.append(url)
        text = self.pages.get(url)
        return Page(url=url, title="t", text=text) if text is not None else None


def hit(url: str, published: str | None = None) -> SearchHit:
    return SearchHit(title="t", url=url, snippet="s", published=published)
