"""Shared fixtures for `tests/orchestrator/`: a loaded built-in `SkillRegistry`, a tool lookup
covering every allowlisted tool the built-in skills name, and a scripted-JSON classifier stub.

No live network — every LLM call is a `StubLLMClient` from `tests.llm.stubs`, matching the
default-profile convention (backend/CLAUDE.md §7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from llm.base import ToolSpec
from schemas.enums import DataClass
from skills.registry import SkillRegistry
from tools.base import Tool, ToolContext

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"


class _ScriptedInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stock_id: str = Field(default="2330")


class _ScriptedOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ran: str


class ScriptedTool:
    """A trivial public tool standing in for any allowlisted capability a lane may call — no
    router/network. Named per-instance so one lookup can cover every built-in skill's
    allowlist."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"scripted stand-in for {name}"
        self.input_model: type[BaseModel] = _ScriptedInput
        self.output_model: type[BaseModel] = _ScriptedOutput
        self.data_class = DataClass.PUBLIC

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        return _ScriptedOutput(ran=self.name)

    def to_tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )


@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load([_BUILT_INS])
    return reg


@pytest.fixture
def tool_lookup(registry: SkillRegistry) -> dict[str, Tool]:
    names: set[str] = set()
    for skill in registry.all_skills():
        names.update(skill.frontmatter.tools)
    return {name: ScriptedTool(name) for name in names}
