"""Every request carries an id through the logs and back to the caller; one structured access line
is written per request; logs render as JSON in production."""

from __future__ import annotations

import io
import json
import logging

import structlog
from fastapi.testclient import TestClient

from config.logging_config import configure_logging, json_logging
from config.settings import Settings


def test_a_response_carries_a_request_id_and_honours_a_sane_incoming_one(
    client: TestClient,
) -> None:
    fresh = client.get("/api/v1/personality/presets")
    echoed = client.get("/api/v1/personality/presets", headers={"X-Request-ID": "trace-abc.123"})
    junk = client.get("/api/v1/personality/presets", headers={"X-Request-ID": "bad id\twith space"})

    assert len(fresh.headers["x-request-id"]) == 32
    assert echoed.headers["x-request-id"] == "trace-abc.123"
    assert junk.headers["x-request-id"] != "bad id\twith space"
    assert len(junk.headers["x-request-id"]) == 32


def test_one_access_line_per_request_with_the_route_template_and_the_id(
    client: TestClient,
) -> None:
    with structlog.testing.capture_logs() as logs:
        response = client.get("/api/v1/personality/presets", headers={"X-Request-ID": "req-1"})

    lines = [entry for entry in logs if entry["event"] == "http_request"]
    assert len(lines) == 1
    assert lines[0]["path"] == "/api/v1/personality/presets"
    assert lines[0]["status"] == response.status_code
    assert lines[0]["method"] == "GET"
    assert lines[0]["duration_ms"] >= 0


def test_probes_are_not_logged(client: TestClient) -> None:
    with structlog.testing.capture_logs() as logs:
        client.get("/livez")

    assert [entry for entry in logs if entry["event"] == "http_request"] == []


def test_production_logs_are_json_and_development_logs_are_not() -> None:
    assert json_logging(Settings(env="production", log_format="auto")) is True
    assert json_logging(Settings(env="development", log_format="auto")) is False
    assert json_logging(Settings(env="development", log_format="json")) is True


def test_a_json_log_line_includes_the_bound_request_context() -> None:
    settings = Settings(env="production", log_format="json", log_level="INFO")
    configure_logging(settings)
    stream = io.StringIO()
    root = logging.getLogger()
    root.handlers[0].setStream(stream)  # type: ignore[attr-defined]
    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="req-9", user_id="u-1")
        structlog.get_logger("test").info("something_happened", detail="x")
        logging.getLogger("some.library").warning("plain stdlib line")
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()
        root.handlers.clear()

    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["event"] == "something_happened"
    assert first["request_id"] == "req-9"
    assert first["user_id"] == "u-1"
    assert first["level"] == "info"
    assert "timestamp" in first
    assert second["event"] == "plain stdlib line"
    assert second["level"] == "warning"
