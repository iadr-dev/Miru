"""A missing or unreachable model answers with a calm, retryable 503 envelope."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api.errors import register_exception_handlers
from llm.local_client import LocalModelUnavailableError
from llm.registry import ProviderNotConfiguredError


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/local-down")
    async def local_down() -> None:  # pyright: ignore[reportUnusedFunction]
        raise LocalModelUnavailableError("http://secret-host:11434")

    @app.get("/no-key")
    async def no_key() -> None:  # pyright: ignore[reportUnusedFunction]
        raise ProviderNotConfiguredError("anthropic_api_key is not configured")

    return app


@pytest.mark.parametrize("path", ["/local-down", "/no-key"])
async def test_model_unavailable_is_a_calm_503(path: str) -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get(path)
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "model_unavailable"
    assert body["retryable"] is True
    assert "secret-host" not in response.text
    assert "api_key" not in response.text
