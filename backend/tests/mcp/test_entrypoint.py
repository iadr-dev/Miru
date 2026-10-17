"""The standalone MCP process provisions the schema on a throwaway event loop and then serves on
uvicorn's own; connections pooled by the first loop must not survive into the second (the first
token check failed with "attached to a different loop")."""

from __future__ import annotations

from typing import Any

import pytest

from mcp import __main__ as entrypoint


class _Engine:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def dispose(self) -> None:
        self._calls.append("dispose")


class _Factory:
    def __init__(self, calls: list[str]) -> None:
        self.kw = {"bind": _Engine(calls)}


async def test_provisioning_drops_its_pooled_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_ensure(factory: Any) -> None:
        del factory
        calls.append("ensure")

    monkeypatch.setattr(entrypoint, "ensure_schema_for", fake_ensure)

    await entrypoint._provision(_Factory(calls))  # type: ignore[arg-type]

    assert calls == ["ensure", "dispose"]
