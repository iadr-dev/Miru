"""`web_search` / `fetch_url` — the two web-access tools, wrapping the `WebSearchGateway`
seam (`dataaccess/web_search.py`) rather than `DataAccessRouter.fetch()`: `WEB` has no
`(capability, market)` entry in `source_priority.yaml` — a query/URL isn't `(capability,
symbol)`-shaped, the same "not a router-fetch-shaped capability" pattern already established
for `get_active_etf_report`/`get_etf_money_flow`'s non-router reads.

**Per-turn enablement toggle:** `POST /agent/query {web_search: true}` (and the matching field
on `regenerate`/`edit`) now decides whether these two tools are visible to the model for a given
turn — wired in `orchestrator.orchestrator.Orchestrator.run`'s `web_search_enabled` parameter,
which appends them to the resolved tool list on the `none`/`single` framework paths only (never
the `subagent`/`team` fan-out lanes, mirroring the `model_override` carve-out). Nothing in
`tools/` enforces the toggle itself — this module only builds and registers the tools; the
orchestrator is what grants or withholds them per turn. `web_search` stays `public` per the
design's own classification even though depth-limiting (`deep_research`) is a separate,
not-yet-built entitlement axis this pass does not implement.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.web_search import WebSearchGateway
from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args

_FETCH_URL_TEXT_CAP = 20_000  # size-capped extraction, per the output-size-control rule


class WebSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1)
    count: int = Field(default=10, ge=1, le=20)


class WebSearchHitOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    snippet: str
    published: str | None = None


class WebSearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    hits: tuple[WebSearchHitOut, ...] = Field(default_factory=tuple)


class WebSearchTool(BaseTool):
    """`web_search` — general web search (query -> cited results with source URLs)."""

    name = "web_search"
    description = (
        "Search the web for a query; returns results with title/URL/snippet, each citable as "
        "a source. Third-party content — never restate a result as our own analysis."
    )
    input_model: type[BaseModel] = WebSearchInput
    output_model: type[BaseModel] = WebSearchOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: WebSearchGateway | None = None) -> None:
        self._gateway = gateway or WebSearchGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(WebSearchInput, args)
        result = await self._gateway.search(
            typed.query, tls_policy=ctx.tls_policy, count=typed.count
        )
        raw_hits = result.payload.get("hits")
        hits: tuple[WebSearchHitOut, ...] = ()
        if isinstance(raw_hits, list):
            typed_hits = cast("list[dict[str, object]]", raw_hits)
            hits = tuple(WebSearchHitOut.model_validate(hit) for hit in typed_hits)
        return WebSearchOutput(currentness_caveat=result.currentness_caveat, hits=hits)


class FetchUrlInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)


class FetchUrlOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    url: str | None = None
    text: str | None = None
    truncated: bool = False


class FetchUrlTool(BaseTool):
    """`fetch_url` — fetch and extract readable text from one specific page, size-capped."""

    name = "fetch_url"
    description = (
        "Fetch a specific web page and return its extracted text (size-capped). Third-party "
        "content — never restate as our own analysis; caveated, never a trading signal."
    )
    input_model: type[BaseModel] = FetchUrlInput
    output_model: type[BaseModel] = FetchUrlOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: WebSearchGateway | None = None) -> None:
        self._gateway = gateway or WebSearchGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(FetchUrlInput, args)
        result = await self._gateway.fetch_url(typed.url, tls_policy=ctx.tls_policy)
        raw_text = result.payload.get("text")
        text = str(raw_text) if isinstance(raw_text, str) else None
        truncated = False
        if text is not None and len(text) > _FETCH_URL_TEXT_CAP:
            text = text[:_FETCH_URL_TEXT_CAP]
            truncated = True
        raw_url = result.payload.get("url")
        return FetchUrlOutput(
            currentness_caveat=result.currentness_caveat,
            url=str(raw_url) if isinstance(raw_url, str) else None,
            text=text,
            truncated=truncated,
        )


def build_registration_table(gateway: WebSearchGateway | None = None) -> list[tuple[Tool, bool]]:
    shared = gateway or WebSearchGateway()
    return [
        (WebSearchTool(shared), False),
        (FetchUrlTool(shared), False),
    ]


__all__ = [
    "FetchUrlInput",
    "FetchUrlOutput",
    "FetchUrlTool",
    "WebSearchHitOut",
    "WebSearchInput",
    "WebSearchOutput",
    "WebSearchTool",
    "build_registration_table",
]
