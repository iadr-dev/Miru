"""What a tool-backed REST route returns, for the OpenAPI spec.

Every `/market/*`, `/stock/*`, `/funds/*`, ... route is a thin projection of one tool: it dispatches
by name and answers with that tool's `output_model` dumped to JSON — or, for a Sponsor-tier tool
asked by an unentitled caller, a `MaskedTeaser` (still HTTP 200). The handlers are annotated
`dict[str, object]` and the result is deliberately not re-validated, so without help the spec says
"some object" and the frontend cannot check what it parses against what the backend sends.

`tool_responses("get_x")` is the route-level declaration of that tool's real response shape. It is
documentation only (routes pair it with `response_model=None`): FastAPI neither validates nor
re-serializes the body, so the wire format is exactly what `dispatch_validated_tool` returned.
`tests/api/test_tool_route_declarations.py` keeps the declaration and the dispatched tool in sync.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from typing import Union

from pydantic import BaseModel

import tools
from schemas.enums import DataClass
from tools.base import BaseTool, MaskedTeaser


def _concrete(cls: type[BaseTool]) -> list[type[BaseTool]]:
    found = [cls]
    for sub in cls.__subclasses__():
        found.extend(_concrete(sub))
    return found


@cache
def _tool_classes() -> dict[str, type[BaseTool]]:
    """Every tool class by its `name`. Importing the whole `tools` package first is what makes the
    subclass walk complete; the composition root imports the same modules to build the registry."""
    for module in pkgutil.walk_packages(tools.__path__, f"{tools.__name__}."):
        importlib.import_module(module.name)
    by_name: dict[str, type[BaseTool]] = {}
    for cls in _concrete(BaseTool):
        name = cls.__dict__.get("name")
        if isinstance(name, str):
            by_name[name] = cls
    return by_name


def tool_responses(*tool_names: str) -> dict[int | str, dict[str, object]]:
    """The OpenAPI `responses=` entry for a route that answers with one of `tool_names`' outputs
    (several when the handler picks the tool by its arguments, e.g. `/search` by market)."""
    models: list[type[BaseModel]] = []
    for tool_name in tool_names:
        cls = _tool_classes()[tool_name]
        models.append(cls.output_model)
        if cls.data_class is DataClass.SPONSOR and not cls.supports_row_preview:
            models.append(MaskedTeaser)  # an unentitled caller gets the lock envelope instead
    unique = tuple(dict.fromkeys(models))
    return {200: {"model": unique[0] if len(unique) == 1 else Union[unique]}}  # noqa: UP007 — built at runtime from a tuple; `|` cannot be


__all__ = ["tool_responses"]
