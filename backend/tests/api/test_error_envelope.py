"""Every error the API returns, including the ones the framework raises, is `{code, message,
retryable}` - never Starlette's bare `{"detail": ...}`."""

from __future__ import annotations

from fastapi.testclient import TestClient

_KEYS = {"code", "message", "retryable"}


def test_an_unknown_route_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/definitely-not-a-route")

    assert response.status_code == 404
    assert set(response.json()) == _KEYS
    assert response.json()["code"] == "not_found"


def test_a_wrong_method_uses_the_envelope_and_keeps_the_allow_header(client: TestClient) -> None:
    response = client.post("/api/v1/market/index-strip")

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


def test_a_validation_error_names_the_offending_field(client: TestClient) -> None:
    response = client.get("/api/v1/market/day-trading")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == _KEYS
    assert "query.symbol" in body["message"]
    assert body["retryable"] is False
