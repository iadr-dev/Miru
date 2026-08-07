"""Shared test helpers for source-adapter tests — recorded-fixture HTTP mocking.

No adapter test in the default profile makes a real network call:
`httpx.MockTransport` replays a fixture file's JSON/text as the response body.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(*parts: str) -> Any:
    path = FIXTURES_ROOT.joinpath(*parts)
    return json.loads(path.read_text(encoding="utf-8"))


def make_json_client_factory(
    body: object, *, status_code: int = 200
) -> Callable[..., httpx.AsyncClient]:
    """Return a drop-in replacement for `sources.tls.build_async_client` that always answers
    with `body` as JSON, regardless of the request — enough for one-fixture-per-test adapter
    tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


@pytest.fixture
def fixture_loader() -> Callable[..., Any]:
    return load_fixture
