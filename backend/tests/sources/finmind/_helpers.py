"""Shared helpers for the FinMind adapter test package — recorded-fixture HTTP mocking glue
reused across every category test module."""

from __future__ import annotations

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from tests.sources.conftest import make_json_client_factory


def settings(token: str | None = "test-token") -> Settings:
    return Settings(finmind_api_key=token, finmind_base_url="https://api.finmindtrade.com/api/v4")


def patch_client(monkeypatch: pytest.MonkeyPatch, body: object, status_code: int = 200) -> None:
    monkeypatch.setattr(
        finmind_module,
        "build_async_client",
        make_json_client_factory(body, status_code=status_code),
    )
