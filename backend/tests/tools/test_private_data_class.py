"""`DataClass.PRIVATE` (one user's own data) in the dispatch gate: it needs a signed-in caller, is
free at every tier, and the refusal for an anonymous caller is a normal result, not an exception.
Every place that switches on `DataClass` is exhaustive over the three values."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from schemas.enums import DataClass, Tier
from tools.base import (
    BaseTool,
    MaskedTeaser,
    SignInRequired,
    ToolContext,
    dispatch_tool,
    is_entitled,
    require_user_id,
)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    ran_for: str


class _PrivateTool(BaseTool):
    name = "private_probe"
    description = "probe"
    input_model: type[BaseModel] = _In
    output_model: type[BaseModel] = _Out
    data_class = DataClass.PRIVATE

    def __init__(self) -> None:
        self.ran = 0

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        self.ran += 1
        return _Out(ran_for=require_user_id(ctx))


def test_is_entitled_is_defined_for_every_data_class() -> None:
    for data_class in DataClass:
        for tier in Tier:
            assert isinstance(is_entitled(data_class, tier), bool)


def test_private_data_is_free_at_every_tier_and_sponsor_still_is_not() -> None:
    assert all(is_entitled(DataClass.PRIVATE, tier) for tier in Tier)
    assert all(is_entitled(DataClass.PUBLIC, tier) for tier in Tier)
    assert not is_entitled(DataClass.SPONSOR, Tier.FREE)
    assert is_entitled(DataClass.SPONSOR, Tier.PREMIUM)


@pytest.mark.parametrize("tier", list(Tier))
async def test_a_signed_in_caller_of_any_tier_runs_a_private_tool(tier: Tier) -> None:
    tool = _PrivateTool()
    result = await dispatch_tool(tool, _In(), ToolContext(user_id="u1", tier=tier))
    assert isinstance(result, _Out)
    assert result.ran_for == "u1"
    assert tool.ran == 1


async def test_an_anonymous_caller_gets_a_typed_refusal_not_an_exception() -> None:
    tool = _PrivateTool()
    result = await dispatch_tool(tool, _In(), ToolContext())
    assert isinstance(result, SignInRequired)
    assert not isinstance(result, MaskedTeaser)
    assert result.requires_sign_in is True
    assert result.tool_name == "private_probe"
    assert tool.ran == 0
    dumped = result.model_dump(mode="json")
    assert set(dumped) == {"requires_sign_in", "tool_name", "message"}


async def test_the_anonymous_refusal_does_not_depend_on_tier() -> None:
    for tier in Tier:
        result = await dispatch_tool(_PrivateTool(), _In(), ToolContext(user_id=None, tier=tier))
        assert isinstance(result, SignInRequired)
