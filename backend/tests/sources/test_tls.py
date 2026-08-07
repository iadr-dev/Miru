"""M6D TLS policy tests — no silent downgrade, loud logging on non-strict."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog

from schemas.enums import TLSPolicy
from sources.tls import build_async_client


@pytest.fixture
def captured_logs() -> Iterator[list[dict[str, object]]]:
    logs: list[dict[str, object]] = []

    def _capture(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        logs.append(event_dict)
        return event_dict

    structlog.configure(processors=[_capture, structlog.processors.JSONRenderer()])
    try:
        yield logs
    finally:
        structlog.reset_defaults()


def test_strict_verifies_by_default(captured_logs: list[dict[str, object]]) -> None:
    build_async_client(tls_policy=TLSPolicy.STRICT)
    assert captured_logs == []


def test_compatibility_logs_loudly(captured_logs: list[dict[str, object]]) -> None:
    build_async_client(tls_policy=TLSPolicy.COMPATIBILITY)
    assert any(entry.get("event") == "tls_policy_non_strict" for entry in captured_logs)


def test_unsafe_explicit_logs_loudly_and_disables_verification(
    captured_logs: list[dict[str, object]],
) -> None:
    build_async_client(tls_policy=TLSPolicy.UNSAFE_EXPLICIT)
    assert any(entry.get("event") == "tls_policy_unsafe_explicit" for entry in captured_logs)
