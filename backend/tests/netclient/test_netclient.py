"""Pooled clients are reused and left open; the retry transport retries safe reads only, with a
bound, and honours Retry-After."""

from __future__ import annotations

import httpx
import pytest

import netclient.retry as retry_module
from netclient import RetryingTransport, aclose_shared_clients, lease_client


async def test_leases_share_one_connection_pool_but_never_one_client() -> None:
    async with lease_client(timeout=5.0) as first:
        pass
    async with lease_client(timeout=9.0, headers={"x": "1"}) as second:
        assert second is not first  # a private client per lease
        assert second._transport is first._transport
        assert second.timeout.read == 9.0
        assert second.headers["x"] == "1"
        assert "x" not in first.headers
    async with lease_client(verify=False) as unverified:
        assert unverified._transport is not first._transport
    await aclose_shared_clients()


async def test_a_lease_does_not_close_the_shared_pool_on_exit() -> None:
    async with lease_client() as client:
        transport = client._transport
    async with lease_client() as again:
        assert again._transport is transport
        assert not again.is_closed
    await aclose_shared_clients()


async def test_cookies_set_for_one_caller_never_reach_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, headers={"set-cookie": "session=caller-a-secret; Path=/"})

    import netclient.shared as shared_module  # noqa: PLC0415

    monkeypatch.setattr(
        shared_module.httpx, "AsyncHTTPTransport", lambda **_kwargs: httpx.MockTransport(handler)
    )
    async with lease_client() as a:
        await a.get("https://upstream.test/x")
        await a.get("https://upstream.test/x")  # the same caller keeps its own cookie
    async with lease_client() as b:
        await b.get("https://upstream.test/x")  # a different caller starts clean
    await aclose_shared_clients()

    assert seen == [None, "session=caller-a-secret", None]


async def test_a_read_that_gets_a_503_is_retried_until_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)
    answers = iter([503, 503, 200])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(next(answers))

    transport = RetryingTransport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://example.test/x")

    assert response.status_code == 200
    assert calls == ["GET", "GET", "GET"]
    assert len(slept) == 2


async def test_retries_are_bounded_and_the_last_answer_is_returned() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    transport = RetryingTransport(httpx.MockTransport(handler), attempts=3, base_delay=0)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://example.test/x")

    assert response.status_code == 503
    assert calls == 3


async def test_a_write_is_never_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    transport = RetryingTransport(httpx.MockTransport(handler), base_delay=0)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://example.test/x", json={})

    assert response.status_code == 503
    assert calls == 1


async def test_retry_after_is_honoured_up_to_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)
    answers = iter([(429, "2"), (429, "120"), (200, "")])

    def handler(request: httpx.Request) -> httpx.Response:
        status, retry_after = next(answers)
        return httpx.Response(status, headers={"retry-after": retry_after} if retry_after else {})

    transport = RetryingTransport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://example.test/x")

    assert slept == [2.0, 5.0]  # 120 s is capped


async def test_a_transient_network_error_is_retried_then_raised_when_it_persists() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom", request=request)

    transport = RetryingTransport(httpx.MockTransport(handler), attempts=2, base_delay=0)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.test/x")

    assert calls == 2
