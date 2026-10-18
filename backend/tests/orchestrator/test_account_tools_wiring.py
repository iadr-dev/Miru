"""Every composition root that builds the tool registry for user-scoped agent turns registers the
`private` account tools (`get_my_*`), and the MCP root deliberately does not. Each root is run for
real (no network at construction) with only `build_tool_registry` wrapped to capture its result."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

import api.composition as api_composition
import scheduler.composition as scheduler_composition
from config.settings import Settings
from mcp._vendor import bootstrap_vendor_sdk
from schemas.enums import DataClass
from tools.registry import ToolRegistry

bootstrap_vendor_sdk()

import mcp.composition as mcp_composition  # noqa: E402

ACCOUNT_TOOLS = {"get_my_watchlists", "get_my_automations", "get_my_usage", "get_my_notifications"}
_JWT = "test-secret-not-a-real-key-0123456789abcdef"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret_key=_JWT,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}",
        parquet_root=(tmp_path / "parquet").as_posix(),
    )


def _capture(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> list[ToolRegistry]:
    built: list[ToolRegistry] = []
    real = module.build_tool_registry

    def recording(*args: object, **kwargs: object) -> ToolRegistry:
        registry = real(*args, **kwargs)
        built.append(registry)
        return registry

    monkeypatch.setattr(module, "build_tool_registry", recording)
    return built


def _names(registry: ToolRegistry) -> set[str]:
    return {tool.name for tool in registry.all_tools()}


def test_api_root_registers_the_account_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built = _capture(monkeypatch, api_composition)
    api_composition.build_app(_settings(tmp_path))
    assert _names(built[0]) >= ACCOUNT_TOOLS


async def test_scheduler_root_registers_the_account_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built = _capture(monkeypatch, scheduler_composition)
    await scheduler_composition.build_scheduler_from_settings(_settings(tmp_path))
    assert _names(built[0]) >= ACCOUNT_TOOLS


def test_mcp_root_never_offers_private_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built = _capture(monkeypatch, mcp_composition)
    mcp_composition.build_mcp_http_app(_settings(tmp_path))
    registry = built[0]
    # Not wired at all, and no private tool of any kind is registered for MCP to offer.
    assert not ACCOUNT_TOOLS & _names(registry)
    assert not [t.name for t in registry.all_tools() if t.data_class is DataClass.PRIVATE]
