"""Every REST router dispatches tools through the one shared `dispatch_validated_tool`.

Four routers used to carry their own copy that lacked its validation handling, so a tool argument
failing a cross-field validator surfaced as a 500 there but a 422 everywhere else. Keeping them
all on the shared function keeps that behaviour uniform.
"""

from __future__ import annotations

import importlib

import pytest

from api.dependencies import dispatch_validated_tool

_ROUTERS_WITH_A_DISPATCH = ("analytics", "context", "market", "themes", "market_rest", "fund_data")


@pytest.mark.parametrize("module_name", _ROUTERS_WITH_A_DISPATCH)
def test_router_uses_the_shared_dispatcher(module_name: str) -> None:
    module = importlib.import_module(f"api.routers.{module_name}")

    assert module._dispatch is dispatch_validated_tool
