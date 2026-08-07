"""Tag-screener tools — the agent's read surface onto the nightly-computed `screener_flags`
dataset plus the catalog that describes what tags exist.

`run_tag_screener` reads the pre-computed flag partition through the derived-store reader and
computes the AND/OR match; it never evaluates tags live per query. It is `public`: the flags are
derived descriptive classification with no scoring and no buy/sell framing, and even the chip
tags rest only on the public three-major-institutional-investor dataset (the same tier at which
`get_institutional_flow` is public) — never the branch-level 分點 data that `get_chip_flows`
gates as sponsor. This follows the established convention that a derived-store read is classed by
what it exposes, not by the origin adapter's tier.

`get_tag_catalog` returns the fixed catalog grouped by category with bilingual labels, so an
agent or frontend can discover the tap tiles.

Unlike the router-backed tool groups, `run_tag_screener` reads the ingest-owned Parquet store
directly (the same shape `get_etf_money_flow` uses for its cross-symbol aggregation), so it takes
a `ParquetMarketDataReader` rather than the data-access router. The blocking DuckDB read runs in a
threadpool so it never stalls the event loop.
"""

from __future__ import annotations

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field

from analytics.screener.catalog import all_tags
from analytics.screener.store import run_tag_screener_query
from ingest.reader import ParquetMarketDataReader
from schemas.enums import DataClass
from schemas.screener import TagScreenerQuery, TagScreenerResult
from tools.base import BaseTool, Tool, ToolContext, parse_args


class RunTagScreenerOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: TagScreenerResult


class RunTagScreenerTool(BaseTool):
    """`run_tag_screener` — match TW symbols against named end-of-day tags (AND/OR) over the
    nightly-computed flag partition. A result is a filtered list matching the described criteria
    ("符合『外資連3買』的個股"), never a recommendation to trade."""

    name = "run_tag_screener"
    description = (
        "Screen Taiwan stocks/ETFs by named end-of-day tags (e.g. 外資連3買, 5日均線回升), "
        "combined AND/OR, returning matching symbols and which tags each satisfied. "
        "Descriptive filter, not trading advice."
    )
    input_model: type[BaseModel] = TagScreenerQuery
    output_model: type[BaseModel] = RunTagScreenerOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "tag_screener"

    def __init__(self, reader: ParquetMarketDataReader) -> None:
        self._reader = reader

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(TagScreenerQuery, args)
        result = await anyio.to_thread.run_sync(self._read, typed)
        return RunTagScreenerOutput(result=result)

    def _read(self, query: TagScreenerQuery) -> TagScreenerResult:
        return run_tag_screener_query(self._reader, query, root=self._reader.root)


class TagCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    label_zh: str
    label_en: str
    description: str
    data_class: DataClass


class TagCatalogCategory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    tags: tuple[TagCatalogEntry, ...] = Field(min_length=1)


class GetTagCatalogInput(BaseModel):
    """No parameters — the catalog is a fixed, market-agnostic enumeration."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GetTagCatalogOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: tuple[TagCatalogCategory, ...] = Field(min_length=1)


class GetTagCatalogTool(BaseTool):
    """`get_tag_catalog` — the discoverable list of screener tags, grouped by category and
    bilingual, so an agent/frontend knows what `run_tag_screener` accepts."""

    name = "get_tag_catalog"
    description = (
        "List the available stock-screener tags, grouped by category (技術面/籌碼面) with "
        "bilingual labels — the tag keys run_tag_screener accepts."
    )
    input_model: type[BaseModel] = GetTagCatalogInput
    output_model: type[BaseModel] = GetTagCatalogOutput
    data_class = DataClass.PUBLIC

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args, ctx
        grouped: dict[str, list[TagCatalogEntry]] = {}
        order: list[str] = []
        for definition in all_tags():
            category = definition.category.value
            if category not in grouped:
                grouped[category] = []
                order.append(category)
            grouped[category].append(
                TagCatalogEntry(
                    key=definition.key,
                    label_zh=definition.label_zh,
                    label_en=definition.label_en,
                    description=definition.description,
                    data_class=definition.data_class,
                )
            )
        categories = tuple(
            TagCatalogCategory(category=category, tags=tuple(grouped[category]))
            for category in order
        )
        return GetTagCatalogOutput(categories=categories)


def build_registration_table(
    reader: ParquetMarketDataReader,
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. Both tools are deferred long-tail
    reads. `run_tag_screener` reads the ingest-owned Parquet flag store, so this table takes the
    derived-store reader rather than the data-access router; `get_tag_catalog` needs neither but
    is registered here alongside its companion."""
    return [
        (RunTagScreenerTool(reader), False),
        (GetTagCatalogTool(), False),
    ]


__all__ = [
    "GetTagCatalogInput",
    "GetTagCatalogOutput",
    "GetTagCatalogTool",
    "RunTagScreenerOutput",
    "RunTagScreenerTool",
    "TagCatalogCategory",
    "TagCatalogEntry",
    "build_registration_table",
]
