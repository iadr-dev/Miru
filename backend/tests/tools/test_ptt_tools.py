from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.ptt_tools import GetPttBoardInput, GetPttBoardTool


class _FakePttGateway:
    """No network — records calls, returns a canned `SourceResult` per board."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
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
            self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
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
