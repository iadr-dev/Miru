"""Shared fixtures for `mcp/` tests — a fake `Tool`/`ToolRegistry` pair (no live network, no
real registry wiring) plus a helper to build the real full-count registry for the handful of
end-to-end tests that need it."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ingest.reader import ParquetMarketDataReader
from llm.base import ToolSpec
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.registry import ToolRegistry


class FakeToolInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: int = Field(ge=0)


class FakeToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doubled: int


class FakeTool:
    """A minimal, real `Tool`-protocol implementation for unit tests — no I/O, no `sources/`."""

    def __init__(
        self,
        name: str,
        description: str,
        *,
        data_class: DataClass = DataClass.PUBLIC,
        raises: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model: type[BaseModel] = FakeToolInput
        self.output_model: type[BaseModel] = FakeToolOutput
        self.data_class = data_class
        self.card_type: str | None = None
        self.supports_row_preview = False
        self._raises = raises

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        if self._raises:
            raise RuntimeError("fake tool failure")
        typed = FakeToolInput.model_validate(args.model_dump())
        return FakeToolOutput(doubled=typed.value * 2)

    def to_tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )


def build_fake_registry() -> ToolRegistry:
    """One immediate tool, a few deferred tools (including a sponsor one and one that always
    raises), and nothing else — enough surface for every unit test in this package."""
    registry = ToolRegistry()
    registry.register(
        FakeTool("fake_immediate", "An immediate-core fake tool for tests."),
        immediate=True,
    )
    registry.register(
        FakeTool("fake_deferred_chip_flows", "Deferred fake tool about chip broker flows."),
        immediate=False,
    )
    registry.register(
        FakeTool(
            "fake_sponsor_tool",
            "A sponsor-tier fake tool — masked unless entitled.",
            data_class=DataClass.SPONSOR,
        ),
        immediate=False,
    )
    registry.register(
        FakeTool("fake_broken_tool", "A fake tool whose run() always raises.", raises=True),
        immediate=False,
    )
    return registry


def build_real_registry() -> ToolRegistry:
    """The real full-count registry, wired against a stub router (no live network) plus a bare
    session factory for the relational theme-lookup/score tools and a `ParquetMarketDataReader`
    over an empty temp directory for the tag-screener tools — for the handful of tests that must
    prove things against real tool objects, not just fakes. These tests never call a theme
    tool's `run()` nor a screener query, so the engine/Parquet tree is never actually read; the
    reader only needs to exist so `run_tag_screener`/`get_tag_catalog` register at all."""
    symbol = Symbol(id="2330", market=Market.TW)
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _ = make_router(symbol, dict.fromkeys(Capability, result))
    session_factory = async_sessionmaker(create_async_engine("sqlite+aiosqlite:///:memory:"))
    market_data_reader = ParquetMarketDataReader(root=Path(tempfile.mkdtemp()))
    return build_default_tool_registry(
        router,
        session_factory=session_factory,
        market_data_reader=market_data_reader,
    )


__all__ = [
    "FakeTool",
    "FakeToolInput",
    "FakeToolOutput",
    "build_fake_registry",
    "build_real_registry",
]
