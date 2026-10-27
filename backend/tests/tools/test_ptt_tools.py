from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.ptt_tools import (
    GetPttBoardInput,
    GetPttBoardTool,
    GetPttStockSearchInput,
    GetPttStockSearchTool,
)


class _FakePttGateway:
    """No network — records calls, returns a canned `SourceResult` per board."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT, lookback_days: int = 1
    ) -> SourceResult:
        self.calls.append(board)
        board_tag = "offtopic" if board in ("Beauty", "Gossiping") else "stock_buzz"
        return SourceResult(
            source_id="PTT",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat=f"caveat:{board_tag}",
            payload={
                "posts": [
                    {
                        "board": board,
                        "board_tag": board_tag,
                        "title": f"[{board}] a post",
                        "post_tag": None,
                        "author": "someone",
                        "push_count": "爆",
                        "link": f"https://ptt.cc/bbs/{board}/fake.html",
                    }
                ]
            },
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


async def test_get_ptt_board_delegates_and_parses_posts() -> None:
    gateway = _FakePttGateway()
    tool = GetPttBoardTool(gateway)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(GetPttBoardInput(board="Stock"), ToolContext())

    assert gateway.calls == ["Stock"]
    assert output.currentness_caveat == "caveat:stock_buzz"
    assert output.posts[0].board == "Stock"
    assert output.posts[0].board_tag == "stock_buzz"
    assert output.posts[0].push_count == "爆"
    assert output.source_id == "PTT"
    assert output.health_status is HealthStatus.OK


async def test_get_ptt_board_defaults_to_stock() -> None:
    gateway = _FakePttGateway()
    tool = GetPttBoardTool(gateway)

    await tool.run(GetPttBoardInput(), ToolContext())

    assert gateway.calls == ["Stock"]


async def test_get_ptt_board_passes_through_offtopic_tag() -> None:
    gateway = _FakePttGateway()
    tool = GetPttBoardTool(gateway)

    output = await tool.run(GetPttBoardInput(board="Beauty"), ToolContext())

    assert output.posts[0].board_tag == "offtopic"
    assert output.currentness_caveat == "caveat:offtopic"


async def test_get_ptt_board_handles_no_posts() -> None:
    class _EmptyGateway:
        async def fetch_board(
            self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT, lookback_days: int = 1
        ) -> SourceResult:
            return SourceResult(
                source_id="PTT",
                data_class=DataClass.PUBLIC,
                timing_class=TimingClass.REFERENCE,
                fetched_at=datetime.now(UTC),
                currentness_caveat="caveat",
                payload={},
                health=SourceHealth(status=HealthStatus.STALE, detail="no posts"),
            )

    tool = GetPttBoardTool(_EmptyGateway())

    output = await tool.run(GetPttBoardInput(board="Warrant"), ToolContext())

    assert output.posts == ()
    assert output.health_status is HealthStatus.STALE


class _StatusGateway:
    """Returns an empty payload carrying the given health, as the adapter does on an outage."""

    def __init__(self, status: HealthStatus) -> None:
        self._status = status

    def _result(self) -> SourceResult:
        return SourceResult(
            source_id="PTT",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="caveat",
            payload={},
            health=SourceHealth(status=self._status, detail="x"),
        )

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT, lookback_days: int = 1
    ) -> SourceResult:
        return self._result()

    async def search_boards(
        self,
        *,
        boards: list[str],
        needles: list[str],
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        lookback_days: int = 7,
    ) -> SourceResult:
        return self._result()


async def test_get_ptt_board_carries_unreachable_so_an_outage_is_not_no_posts() -> None:
    tool = GetPttBoardTool(_StatusGateway(HealthStatus.UNREACHABLE))

    output = await tool.run(GetPttBoardInput(), ToolContext())

    assert output.posts == ()
    assert output.health_status is HealthStatus.UNREACHABLE
    assert output.model_dump(mode="json")["health_status"] == "unreachable"


async def test_get_ptt_stock_search_carries_unreachable() -> None:
    tool = GetPttStockSearchTool(_StatusGateway(HealthStatus.UNREACHABLE))

    output = await tool.run(
        GetPttStockSearchInput(symbol="2330", company_name="台積電"), ToolContext()
    )

    assert output.posts == ()
    assert output.health_status is HealthStatus.UNREACHABLE


async def test_get_ptt_stock_search_carries_ok() -> None:
    tool = GetPttStockSearchTool(_StatusGateway(HealthStatus.OK))

    output = await tool.run(
        GetPttStockSearchInput(symbol="2330", company_name="台積電"), ToolContext()
    )

    assert output.health_status is HealthStatus.OK
