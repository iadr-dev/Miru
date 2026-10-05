"""Binding the ingest daemon's real `data.updated` publisher by config name.

`ingest/` may not import `events/`, so it resolves a `"module:callable"` factory from settings. The
contract these tests pin: the default binds the real Redis publisher; the in-memory bus (which can't
cross processes) honestly stays unbound; and ANY wiring problem degrades to the no-op instead of
stopping a data pull."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from events.data_updates import RedisDataUpdatePublisher
from ingest.daemon import build_ingest_context_from_settings
from ingest.data_updates import NoOpDataUpdatePublisher
from ingest.publisher_binding import resolve_data_update_publisher
from schemas.enums import Capability, Market


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[Capability, Market, str, str]] = []

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        self.calls.append((capability, market, as_of, scope))


_RECORDER = RecordingPublisher()


def recording_factory(settings: Settings) -> RecordingPublisher:
    del settings
    return _RECORDER


def declining_factory(settings: Settings) -> None:
    del settings


def exploding_factory(settings: Settings) -> RecordingPublisher:
    raise RuntimeError("redis unreachable at build time")


def unusable_factory(settings: Settings) -> object:
    del settings
    return object()  # no publish_data_updated


def _settings(**overrides: object) -> Settings:
    return Settings(**{"finmind_api_key": "t", **overrides})  # type: ignore[arg-type]


_HERE = "tests.ingest.test_publisher_binding"


def test_default_setting_names_the_real_factory() -> None:
    assert (
        Settings(finmind_api_key="t").ingest_data_update_publisher_factory
        == "events.ingest_wiring:build_data_update_publisher"
    )


def test_redis_backend_binds_the_real_cross_process_publisher() -> None:
    publisher = resolve_data_update_publisher(_settings(event_bus_backend="redis"))
    assert isinstance(publisher, RedisDataUpdatePublisher)


def test_memory_backend_stays_on_the_noop_because_nothing_could_read_it() -> None:
    # An in-process bus in the ingest process is unreachable from the API/scheduler processes:
    # binding it would look wired while delivering to nobody.
    publisher = resolve_data_update_publisher(_settings(event_bus_backend="memory"))
    assert isinstance(publisher, NoOpDataUpdatePublisher)


def test_an_empty_setting_disables_binding() -> None:
    settings = _settings(event_bus_backend="redis", ingest_data_update_publisher_factory="")
    assert isinstance(resolve_data_update_publisher(settings), NoOpDataUpdatePublisher)
    blank = _settings(event_bus_backend="redis", ingest_data_update_publisher_factory="   ")
    assert isinstance(resolve_data_update_publisher(blank), NoOpDataUpdatePublisher)


def test_a_custom_factory_is_resolved_by_name() -> None:
    settings = _settings(ingest_data_update_publisher_factory=f"{_HERE}:recording_factory")
    assert resolve_data_update_publisher(settings) is _RECORDER


@pytest.mark.parametrize(
    "spec",
    [
        "no_colon_here",
        ":missing_module",
        "events.ingest_wiring:",
        "does.not.exist:factory",
        f"{_HERE}:no_such_attribute",
        f"{_HERE}:exploding_factory",
        f"{_HERE}:declining_factory",
        f"{_HERE}:unusable_factory",
    ],
)
def test_any_wiring_problem_degrades_to_noop_and_never_raises(spec: str) -> None:
    # Ingest must always land its partitions; a bad binding cannot be allowed to stop a pull.
    publisher = resolve_data_update_publisher(_settings(ingest_data_update_publisher_factory=spec))
    assert isinstance(publisher, NoOpDataUpdatePublisher)


async def test_the_daemon_context_builder_installs_the_resolved_publisher(tmp_path: Path) -> None:
    """The point of the binding: the context the ingest process actually runs with carries the
    publisher (`build_ingest_context_from_settings` used to leave the no-op in place, so no deployed
    stack ever delivered `data.updated`). Jobs calling whatever publisher the context holds is
    covered by `test_data_updated_signal.py`."""
    settings = _settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}",
        parquet_root=str(tmp_path / "parquet"),
        ingest_data_update_publisher_factory=f"{_HERE}:recording_factory",
    )
    ctx = await build_ingest_context_from_settings(settings)
    assert ctx.data_update_publisher is _RECORDER


async def test_the_daemon_context_defaults_to_the_real_publisher_on_the_redis_bus(
    tmp_path: Path,
) -> None:
    settings = _settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}",
        parquet_root=str(tmp_path / "parquet"),
        event_bus_backend="redis",
    )
    ctx = await build_ingest_context_from_settings(settings)
    assert isinstance(ctx.data_update_publisher, RedisDataUpdatePublisher)
