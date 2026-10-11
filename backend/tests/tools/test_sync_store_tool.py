"""`SyncStoreTool` runs a tool's blocking body in a worker thread, so a slow Parquet read cannot
freeze the event loop; and no tool may do blocking store work directly inside an `async def run`.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from schemas.enums import DataClass, Market, Tier
from tools.base import SyncStoreTool, ToolContext

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "tools"


class _Empty(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Probe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: int


class _SlowTool(SyncStoreTool):
    name = "slow"
    description = "blocks for a while"
    input_model = _Empty
    output_model = _Probe
    data_class = DataClass.PUBLIC

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        time.sleep(0.4)  # stands in for a long Parquet read
        return _Probe(thread_id=threading.get_ident())


async def test_the_blocking_body_runs_off_the_event_loop_thread() -> None:
    result = await _SlowTool().run(_Empty(), ToolContext())

    assert isinstance(result, _Probe)
    assert result.thread_id != threading.get_ident()


async def test_other_work_proceeds_while_a_blocking_tool_runs() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    await _SlowTool().run(_Empty(), ToolContext())
    task.cancel()

    # Run inline, the 0.4 s sleep would starve the loop and the ticker would barely tick.
    assert ticks >= 15


def test_no_tool_reads_the_store_inside_an_async_run_without_awaiting() -> None:
    """Architecture guard: an `async def run` that never awaits and touches `self._store` is pure
    blocking work on the event loop. Make it a `SyncStoreTool` (or move its reads behind
    `anyio.to_thread.run_sync`)."""
    offenders: list[str] = []
    for path in sorted(_TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not (isinstance(fn, ast.AsyncFunctionDef) and fn.name == "run"):
                    continue
                awaits = any(isinstance(n, ast.Await) for n in ast.walk(fn))
                uses_store = any(
                    isinstance(n, ast.Attribute) and n.attr == "_store" for n in ast.walk(fn)
                )
                if uses_store and not awaits:
                    offenders.append(f"{path.relative_to(_TOOLS_DIR).as_posix()}::{cls.name}")

    assert offenders == [], f"blocking store reads on the event loop: {offenders}"


# ---- the freshness-keyed result cache ------------------------------------------------------------


class _FakeStore:
    """Stands in for `ParquetStore`: the cache only asks it for a freshness token."""

    def __init__(self) -> None:
        self.token: tuple[tuple[str, int], ...] = (("dataset=prices", 1),)

    def freshness_token(self, market: str) -> tuple[tuple[str, int], ...]:
        return self.token


class _MarketArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    top: int = 10


class _CountedOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call: int


class _CachedTool(SyncStoreTool):
    name = "cached"
    description = "counts how often it really computes"
    input_model = _MarketArgs
    output_model = _CountedOutput
    data_class = DataClass.PUBLIC
    cache_ttl_seconds = 60.0

    def __init__(self, store: _FakeStore, *, fail: bool = False) -> None:
        self._store = store
        self.calls = 0
        self.fail = fail

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        time.sleep(0.05)
        self.calls += 1
        if self.fail:
            msg = "boom"
            raise RuntimeError(msg)
        return _CountedOutput(call=self.calls)


async def test_a_repeat_call_reuses_the_result_while_the_store_is_unchanged() -> None:
    tool = _CachedTool(_FakeStore())

    first = await tool.run(_MarketArgs(), ToolContext())
    second = await tool.run(_MarketArgs(), ToolContext())

    assert first is second
    assert tool.calls == 1


async def test_new_ingest_data_invalidates_the_cached_result() -> None:
    store = _FakeStore()
    tool = _CachedTool(store)
    await tool.run(_MarketArgs(), ToolContext())

    store.token = (("dataset=prices", 2),)  # ingest wrote a new partition
    await tool.run(_MarketArgs(), ToolContext())

    assert tool.calls == 2


async def test_different_arguments_and_tiers_do_not_share_a_result() -> None:
    tool = _CachedTool(_FakeStore())

    await tool.run(_MarketArgs(top=10), ToolContext())
    await tool.run(_MarketArgs(top=20), ToolContext())
    await tool.run(_MarketArgs(top=10), ToolContext(tier=Tier.PRO))

    assert tool.calls == 3


async def test_identical_simultaneous_requests_compute_once() -> None:
    tool = _CachedTool(_FakeStore())

    results = await asyncio.gather(*(tool.run(_MarketArgs(), ToolContext()) for _ in range(6)))

    assert tool.calls == 1
    assert len({id(result) for result in results}) == 1


async def test_a_failed_computation_is_not_cached() -> None:
    tool = _CachedTool(_FakeStore(), fail=True)
    with pytest.raises(RuntimeError):
        await tool.run(_MarketArgs(), ToolContext())

    tool.fail = False
    result = await tool.run(_MarketArgs(), ToolContext())

    assert isinstance(result, _CountedOutput)
    assert tool.calls == 2


async def test_a_tool_without_a_ttl_never_caches() -> None:
    tool = _CachedTool(_FakeStore())
    tool.cache_ttl_seconds = 0.0

    await tool.run(_MarketArgs(), ToolContext())
    await tool.run(_MarketArgs(), ToolContext())

    assert tool.calls == 2
