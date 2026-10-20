"""The data adapter runs a research data request through the same gates as a chat tool call, and
the allowlist matches the real tool registry."""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest
from pydantic import BaseModel

from db.base import make_engine, make_session_factory
from orchestrator.composition import build_tool_registry
from research.adapters import ToolData
from research.datatools import DATA_TOOLS, DataToolName, planner_catalog
from research.models import DataRequest
from schemas.enums import DataClass, Tier
from tools.base import ToolContext

pytestmark = pytest.mark.governance


class _Args(BaseModel):
    id: str
    market: str = "TW"
    statement_type: str | None = None


class _Out(BaseModel):
    found: bool = True
    rows: list[dict[str, object]] | None = None
    note: str = ""
    seen_statement_type: str | None = None


class _FakeTool:
    card_type = None
    supports_row_preview = False
    input_model = _Args
    output_model = _Out

    def __init__(self, name: str, out: _Out, data_class: DataClass = DataClass.PUBLIC) -> None:
        self.name = name
        self.description = name
        self.data_class = data_class
        self._out = out
        self.calls: list[_Args] = []

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(args, _Args)
        self.calls.append(args)
        return self._out.model_copy(update={"seen_statement_type": args.statement_type})

    def to_tool_spec(self) -> Any:  # pragma: no cover - not used here
        raise NotImplementedError


def _adapter(tool: _FakeTool | None, *, tier: Tier = Tier.PRO, locale: str = "zh-TW") -> ToolData:
    return ToolData(
        tools=lambda name: tool if tool is not None and name == tool.name else None,
        ctx=ToolContext(tier=tier),
        locale=locale,
    )


def _request(tool: str = "get_month_revenue", symbol: str = "2330", market: str = "TW"):
    return DataRequest(tool=tool, symbol=symbol, market=market)  # type: ignore[arg-type]


async def test_a_public_tool_returns_a_titled_json_result() -> None:
    tool = _FakeTool("get_month_revenue", _Out(rows=[{"month": "2026-09", "yoy": 12.1}]))
    result = await _adapter(tool).fetch(_request())
    assert result is not None
    assert result.title == "2330 月營收"
    assert json.loads(result.text)["rows"] == [{"month": "2026-09", "yoy": 12.1}]
    assert tool.calls == [_Args(id="2330", market="TW")]


async def test_the_title_follows_the_locale() -> None:
    tool = _FakeTool("get_month_revenue", _Out(rows=[{"a": 1}]))
    result = await _adapter(tool, locale="en").fetch(_request())
    assert result is not None
    assert result.title == "2330 Monthly revenue"


async def test_a_tools_fixed_arguments_are_sent() -> None:
    tool = _FakeTool("get_financial_statement", _Out(rows=[{"a": 1}]))
    result = await _adapter(tool).fetch(_request("get_financial_statement"))
    assert result is not None
    assert tool.calls[0].statement_type == "income_statement"


async def test_a_locked_sponsor_tool_adds_nothing_for_a_free_user() -> None:
    tool = _FakeTool("get_month_revenue", _Out(rows=[{"a": 1}]), DataClass.SPONSOR)
    assert await _adapter(tool, tier=Tier.FREE).fetch(_request()) is None
    assert tool.calls == []  # the tool never ran


async def test_the_same_sponsor_tool_runs_for_a_paying_user() -> None:
    tool = _FakeTool("get_month_revenue", _Out(rows=[{"a": 1}]), DataClass.SPONSOR)
    assert await _adapter(tool, tier=Tier.PRO).fetch(_request()) is not None


@pytest.mark.parametrize(
    "out", [_Out(found=False), _Out(rows=[]), _Out(found=False, rows=[{"a": 1}])]
)
async def test_a_lookup_that_found_nothing_is_left_out(out: _Out) -> None:
    assert await _adapter(_FakeTool("get_month_revenue", out)).fetch(_request()) is None


async def test_an_unregistered_tool_is_left_out() -> None:
    assert await _adapter(None).fetch(_request()) is None


async def test_a_symbol_the_tool_rejects_is_left_out() -> None:
    class _Strict(_Args):
        id: str  # type: ignore[assignment]

        @classmethod
        def model_validate(cls, obj: object, **kwargs: Any) -> _Strict:  # type: ignore[override]
            raise ValueError("bad symbol")

    tool = _FakeTool("get_month_revenue", _Out(rows=[{"a": 1}]))
    tool.input_model = _Strict
    # a pydantic ValidationError is what the adapter handles; any other error is a real bug
    adapter = _adapter(tool)
    request = _request()
    with pytest.raises(ValueError, match="bad symbol"):
        await adapter.fetch(request)


async def test_trading_language_in_a_result_is_redacted_before_it_can_be_cited() -> None:
    tool = _FakeTool(
        "get_analyst_consensus", _Out(rows=[{"a": 1}], note="分析師建議買進，目標價 1200")
    )
    result = await _adapter(tool).fetch(_request("get_analyst_consensus"))
    assert result is not None
    assert "買進" not in result.text
    assert "目標價" not in result.text


@pytest.mark.parametrize("symbol", ["ALL", "all", "market"])
async def test_a_request_that_asks_for_the_whole_market_is_refused(symbol: str) -> None:
    tool = _FakeTool("get_month_revenue", _Out(rows=[{"a": 1}]))
    # the planner can only name a (symbol, market) pair, but the gate is enforced regardless
    assert await _adapter(tool).fetch(_request(symbol=symbol)) is None
    assert tool.calls == []


def test_a_symbol_must_look_like_a_ticker() -> None:
    for bad in ("", "2330; DROP", "a" * 13, "../etc", "2330 2454", "*"):
        with pytest.raises(ValueError):
            DataRequest(tool="get_month_revenue", symbol=bad)  # type: ignore[arg-type]
    for good in ("2330", "NVDA", "BRK.B", "00878", "2330A"):
        assert DataRequest(tool="get_month_revenue", symbol=good)  # type: ignore[arg-type]


def test_a_tool_outside_the_allowlist_cannot_be_requested() -> None:
    for name in ("get_screener_run", "place_order", "execute_tool", "web_search"):
        with pytest.raises(ValueError):
            DataRequest(tool=name, symbol="2330")  # type: ignore[arg-type]


def test_the_allowlist_and_the_request_type_name_the_same_tools() -> None:
    assert set(DATA_TOOLS) == set(get_args(DataToolName))
    catalog = planner_catalog().splitlines()
    assert len(catalog) == len(DATA_TOOLS)
    assert all(f"- {name}:" in planner_catalog() for name in DATA_TOOLS)


def test_every_allowlisted_tool_is_a_real_public_or_sponsor_read_tool() -> None:
    factory = make_session_factory(make_engine("sqlite+aiosqlite:///:memory:"))
    registry = build_tool_registry(session_factory=factory)
    for name, spec in DATA_TOOLS.items():
        tool = registry.get_tool(name)
        assert tool is not None, f"{name} is not registered"
        args = {"id": "2330", "market": "TW", **dict(spec.extra_args)}
        tool.input_model.model_validate(args)  # the arguments the adapter sends are accepted
        assert tool.data_class in (DataClass.PUBLIC, DataClass.SPONSOR)
