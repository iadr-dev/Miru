"""The Postgres engine is built with health checks, a bounded pool and a statement cap; SQLite
keeps its defaults. The engine factory is intercepted, so no database is needed."""

from __future__ import annotations

import pytest

from db import base

_PG_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_create_async_engine(url: str, **kwargs: object) -> object:
        seen["url"] = url
        seen.update(kwargs)
        return object()

    for name in (
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_RECYCLE_SECONDS",
        "DB_STATEMENT_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(base, "create_async_engine", fake_create_async_engine)
    return seen


def test_postgres_engine_checks_connections_and_bounds_the_pool(
    captured: dict[str, object],
) -> None:
    base.make_engine(_PG_URL)

    assert captured["pool_pre_ping"] is True
    assert captured["pool_size"] == 10
    assert captured["max_overflow"] == 10
    assert captured["pool_recycle"] == 1800
    assert captured["pool_timeout"] == 10
    assert captured["connect_args"] == {
        "server_settings": {
            "statement_timeout": "30000",
            "idle_in_transaction_session_timeout": "300000",
        }
    }


def test_pool_and_timeout_come_from_settings(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "5000")

    base.make_engine(_PG_URL)

    assert captured["pool_size"] == 3
    assert captured["connect_args"] == {
        "server_settings": {
            "statement_timeout": "5000",
            "idle_in_transaction_session_timeout": "300000",
        }
    }


def test_a_zero_timeout_means_no_statement_cap(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "0")

    base.make_engine(_PG_URL)

    assert captured["connect_args"] == {
        "server_settings": {"idle_in_transaction_session_timeout": "300000"}
    }


def test_both_server_side_caps_can_be_switched_off(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "0")
    monkeypatch.setenv("DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "0")

    base.make_engine(_PG_URL)

    assert captured["connect_args"] == {}


def test_the_pool_wait_is_configurable(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "4")

    base.make_engine(_PG_URL)

    assert captured["pool_timeout"] == 4


def test_sqlite_engine_is_left_at_its_defaults(captured: dict[str, object]) -> None:
    base.make_engine("sqlite+aiosqlite:///:memory:")

    assert "pool_pre_ping" not in captured
    assert "pool_size" not in captured
