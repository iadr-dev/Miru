"""A tool-backed REST route declares its response shape (`tool_responses(...)` in the decorator) and
dispatches the same tool by name in its body. The two are written separately, so this keeps them
from drifting: the spec the frontend is checked against must describe what the route really sends.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parents[2] / "src" / "api" / "routers"
_VERBS = {"get", "post", "put", "patch", "delete"}


def _declared(function: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str] | None:
    """Tool names in the route decorator's `responses=tool_responses(...)`, or None if absent."""
    for decorator in function.decorator_list:
        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
            continue
        if decorator.func.attr not in _VERBS:
            continue
        for keyword in decorator.keywords:
            value = keyword.value
            if (
                keyword.arg == "responses"
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "tool_responses"
            ):
                return {
                    arg.value
                    for arg in value.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                }
    return None


def _dispatched(function: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[set[str], bool]:
    """(literal tool names passed to `_dispatch`, whether any call picks the tool dynamically)."""
    names: set[str] = set()
    dynamic = False
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_dispatch"
            and len(node.args) >= 2
        ):
            tool = node.args[1]
            if isinstance(tool, ast.Constant) and isinstance(tool.value, str):
                names.add(tool.value)
            else:
                dynamic = True
    return names, dynamic


def _routes() -> list[tuple[str, str, set[str] | None, set[str], bool]]:
    found = []
    for path in sorted(ROUTERS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                dispatched, dynamic = _dispatched(node)
                if dispatched or dynamic:
                    found.append((path.name, node.name, _declared(node), dispatched, dynamic))
    return found


ROUTES = _routes()


def test_the_scan_finds_the_tool_backed_routes() -> None:
    assert len(ROUTES) > 100  # a broken scan would pass everything below vacuously


@pytest.mark.parametrize(("module", "handler", "declared", "dispatched", "dynamic"), ROUTES)
def test_the_declared_tools_are_the_dispatched_tools(
    module: str, handler: str, declared: set[str] | None, dispatched: set[str], dynamic: bool
) -> None:
    where = f"{module}::{handler}"
    assert declared is not None, f"{where} dispatches a tool but declares no tool_responses(...)"
    if dynamic:
        # the tool is chosen at runtime, so the declaration must at least cover every literal
        assert declared >= dispatched, where
    else:
        assert declared == dispatched, f"{where}: declares {declared}, dispatches {dispatched}"
