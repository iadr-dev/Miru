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

from typing import Literal

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.screener.catalog import all_tags
from analytics.screener.engine import RunScreenerInput, RunScreenerOutput, run_screener
from analytics.screener.factor_catalog import FactorCatalog, get_factor_catalog
from analytics.screener.presets import ScreenerPreset, get_screener_presets
from analytics.screener.store import run_tag_screener_query
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
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


class GetFactorCatalogInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["TW", "US", "ETF"]


class GetFactorCatalogOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog: FactorCatalog


class GetFactorCatalogTool(BaseTool):
    """`get_factor_catalog` — the per-market screener factor/signal catalog the condition
    builder discovers from (README.md §2.1). Pure lookup, no I/O."""

    name = "get_factor_catalog"
    description = (
        "Get the per-market screener factor catalog (TW/US/ETF): filterable numeric fields "
        "plus, for TW, boolean signal tags. Every factor maps to a real, already-computable "
        "input — never an aspirational field."
    )
    input_model: type[BaseModel] = GetFactorCatalogInput
    output_model: type[BaseModel] = GetFactorCatalogOutput
    data_class = DataClass.PUBLIC

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetFactorCatalogInput, args)
        return GetFactorCatalogOutput(catalog=get_factor_catalog(typed.market))


class GetScreenerPresetsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["TW", "US", "ETF"]


class GetScreenerPresetsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    presets: tuple[ScreenerPreset, ...] = Field(default_factory=tuple)


class GetScreenerPresetsTool(BaseTool):
    """`get_screener_presets` — named starting-point condition sets the frontend's
    `PresetLibrary` loads into the builder (README.md §3). A preset is a visible, editable
    starting point, never a black-box recommendation."""

    name = "get_screener_presets"
    description = (
        "Get the named screener presets for one market (TW/US/ETF) — each a bundle of "
        "conditions a user can load into the condition builder and tweak. Descriptive "
        "starting points, never trading advice."
    )
    input_model: type[BaseModel] = GetScreenerPresetsInput
    output_model: type[BaseModel] = GetScreenerPresetsOutput
    data_class = DataClass.PUBLIC

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetScreenerPresetsInput, args)
        return GetScreenerPresetsOutput(presets=get_screener_presets(typed.market))


class RunScreenerOutputWrapper(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: RunScreenerOutput


class RunScreenerTool(BaseTool):
    """`run_screener` — the gates→setup funnel (`analytics/screener/engine.py`): binary
    elimination (liquidity/price/市值/處置/注意) followed by AND/OR condition matching, returning
    a filtered symbol list, never a ranked/recommended one. Rank (stage ⓒ) is a separate call
    (`get_top_stocks`/`get_score_leaderboard`) over this call's survivors."""

    name = "run_screener"
    description = (
        "Run the gates->setup screener funnel for one market (TW/US/ETF): apply hard "
        "liquidity/price/market-cap/disposition gates, then AND/OR-match the requested factor "
        "conditions, returning the filtered symbol list. A descriptive filter result "
        "('符合條件的清單，非推薦名單'), never a buy/sell recommendation. Set count_only=true "
        "for a cheap survivor-count probe (e.g. a live builder UI) without hydrating rows."
    )
    input_model: type[BaseModel] = RunScreenerInput
    output_model: type[BaseModel] = RunScreenerOutputWrapper
    data_class = DataClass.PUBLIC
    card_type: str | None = "screener_result"

    def __init__(
        self,
        parquet_store: ParquetStore,
        tag_reader: ParquetMarketDataReader,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._parquet_store = parquet_store
        self._tag_reader = tag_reader
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(RunScreenerInput, args)
        result = await run_screener(
            typed,
            parquet_store=self._parquet_store,
            tag_reader=self._tag_reader,
            session_factory=self._session_factory,
        )
        return RunScreenerOutputWrapper(result=result)


def build_registration_table(
    reader: ParquetMarketDataReader,
    *,
    parquet_store: ParquetStore | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. `run_tag_screener`/`get_tag_catalog`
    are deferred long-tail reads needing only the derived-store reader. `get_factor_catalog`/
    `get_screener_presets` are pure lookups, no dependencies. `run_screener` additionally needs
    the Parquet store (gates + ETF numeric reads) and the DB session factory (TW `stock_scores`
    numeric reads) — registered only when both are supplied, matching `theme_group`/`score_group`'s
    optional-dependency shape in `tools/__init__.py` so a narrow test wiring only the router-backed
    set still composes without it."""
    table: list[tuple[Tool, bool]] = [
        (RunTagScreenerTool(reader), False),
        (GetTagCatalogTool(), False),
        (GetFactorCatalogTool(), False),
        (GetScreenerPresetsTool(), False),
    ]
    if parquet_store is not None and session_factory is not None:
        table.append((RunScreenerTool(parquet_store, reader, session_factory), False))
    return table


__all__ = [
    "GetFactorCatalogInput",
    "GetFactorCatalogOutput",
    "GetFactorCatalogTool",
    "GetScreenerPresetsInput",
    "GetScreenerPresetsOutput",
    "GetScreenerPresetsTool",
    "GetTagCatalogInput",
    "GetTagCatalogOutput",
    "GetTagCatalogTool",
    "RunScreenerOutputWrapper",
    "RunScreenerTool",
    "RunTagScreenerOutput",
    "RunTagScreenerTool",
    "TagCatalogCategory",
    "TagCatalogEntry",
    "build_registration_table",
]
