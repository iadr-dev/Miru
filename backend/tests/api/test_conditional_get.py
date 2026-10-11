"""Conditional GET: JSON reads carry a body-derived ETag and answer 304 when the client has it."""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from starlette.types import Receive, Scope, Send

from api.etag import ConditionalGetMiddleware, etag_for


def test_a_json_read_gets_an_etag_and_revalidates_with_304(client: TestClient) -> None:
    first = client.get("/api/v1/personality/presets")
    etag = first.headers["etag"]

    second = client.get("/api/v1/personality/presets", headers={"If-None-Match": etag})

    assert first.status_code == 200
    assert etag.startswith('W/"')
    assert first.headers["cache-control"] == "private, no-cache"
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == etag


def test_a_different_tag_gets_the_full_body_again(client: TestClient) -> None:
    response = client.get("/api/v1/personality/presets", headers={"If-None-Match": 'W/"stale"'})

    assert response.status_code == 200
    assert response.content


def test_errors_are_never_tagged(client: TestClient) -> None:
    response = client.get("/api/v1/market/day-trading")  # 422: missing symbol

    assert response.status_code == 422
    assert "etag" not in response.headers


async def _call(app: object, *, method: str = "GET", headers: list[tuple[bytes, bytes]] = ()):  # type: ignore[assignment]
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        return await http.request(method, "/", headers=dict(headers))


def _json_app(*, extra_headers: list[tuple[bytes, bytes]] = ()):  # type: ignore[assignment]
    body = json.dumps({"n": 1}).encode()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), *extra_headers],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return ConditionalGetMiddleware(app)


async def test_the_tag_is_a_hash_of_the_exact_body_and_matches_weakly() -> None:
    first = await _call(_json_app())
    tag = first.headers["etag"]

    assert tag == etag_for(first.content)
    for header in (tag, tag.removeprefix("W/"), f'"other", {tag}', "*"):
        again = await _call(_json_app(), headers=[(b"if-none-match", header.encode())])
        assert again.status_code == 304, header


async def test_a_response_that_sets_a_cookie_is_not_tagged() -> None:
    response = await _call(_json_app(extra_headers=[(b"set-cookie", b"a=b")]))

    assert "etag" not in response.headers


async def test_writes_and_streams_pass_through_untouched() -> None:
    async def sse(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True})
        await send({"type": "http.response.body", "body": b"data: 2\n\n"})

    stream = await _call(ConditionalGetMiddleware(sse))
    write = await _call(_json_app(), method="POST")

    assert stream.content == b"data: 1\n\ndata: 2\n\n"
    assert "etag" not in stream.headers
    assert "etag" not in write.headers
