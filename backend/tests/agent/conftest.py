"""Shared fakes for `tests/agent/`: a minimal `Tool` that needs no router/network, for
exercising the loop's dispatch path without pulling in the whole tools/ stack."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from llm.base import ToolSpec
from schemas.enums import DataClass
from tools.base import ToolContext


class EchoInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)


class EchoOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    echoed: str


class EchoTool:
    """A trivial `public` tool: echoes its input back. No network, no router — used to
    exercise the loop's tool-dispatch plumbing in isolation."""

    name = "echo_tool"
    description = "Echoes back the given text."
    input_model: type[BaseModel] = EchoInput
    output_model: type[BaseModel] = EchoOutput
    data_class = DataClass.PUBLIC

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = EchoInput.model_validate(args.model_dump())
        return EchoOutput(echoed=typed.text)

    def to_tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )


__all__ = ["EchoInput", "EchoOutput", "EchoTool"]
