"""The Client ID Metadata Document fetcher: the SSRF fence is the point.

The network is a `httpx.MockTransport` (there is no public host to call from a test) and the name
service is a stub; everything the fetcher DECIDES - which addresses are allowed, where the request
is pinned, what it sends, how it bounds the answer - is the real code under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from sources.oauth_client_metadata import (
    ClientMetadataFetcher,
    ClientMetadataFetchError,
    is_public_address,
)

URL = "https://app.example.com/oauth/client.json"
PUBLIC_IP = "93.184.216.34"
DOCUMENT = {
    "client_id": URL,
    "client_name": "Example",
    "redirect_uris": ["http://localhost:8787/callback"],
}


def resolves_to(*addresses: str):
    async def resolver(host: str, port: int) -> list[str]:
        del host, port
        return list(addresses)

    return resolver


def fetcher_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    resolver=None,
    max_bytes: int = 65_536,
    timeout_seconds: float = 5.0,
) -> ClientMetadataFetcher:
    return ClientMetadataFetcher(
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        resolver=resolver or resolves_to(PUBLIC_IP),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"the fetcher must not have connected: {request.url}")


async def reason_of(fetcher: ClientMetadataFetcher, url: str = URL) -> str:
    with pytest.raises(ClientMetadataFetchError) as excinfo:
        await fetcher.fetch(url)
    return excinfo.value.reason


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "93.184.216.34",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",  # an IPv4-mapped address is judged by the IPv4 inside it
    ],
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address)


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "169.254.169.254",  # AWS / GCP / Azure instance metadata
        "fd00:ec2::254",  # AWS IPv6 instance metadata
        "fe80::1",
        "100.64.0.1",  # carrier-grade NAT
        "100.100.100.200",  # Alibaba Cloud metadata
        "192.0.0.192",  # Oracle Cloud metadata
        "0.0.0.0",  # noqa: S104 - the address under test
        "224.0.0.1",
        "255.255.255.255",
        "::ffff:10.0.0.1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "not-an-address",
        "",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/client.json",
        "https://10.1.2.3/client.json",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/client.json",
        "https://[fd00:ec2::254]/client.json",
    ],
)
async def test_an_ip_literal_in_a_private_range_is_refused_without_connecting(url: str) -> None:
    assert await reason_of(fetcher_for(never_called), url) == "blocked_address"


async def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"):
        fetcher = fetcher_for(never_called, resolver=resolves_to(address))
        assert await reason_of(fetcher) == "blocked_address", address


async def test_one_private_address_among_public_ones_refuses_the_whole_fetch() -> None:
    fetcher = fetcher_for(never_called, resolver=resolves_to(PUBLIC_IP, "10.0.0.5"))
    assert await reason_of(fetcher) == "blocked_address"


async def test_dns_failures_are_reported_not_raised_raw() -> None:
    async def failing(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    assert await reason_of(fetcher_for(never_called, resolver=failing)) == "dns_failure"
    assert await reason_of(fetcher_for(never_called, resolver=resolves_to())) == "dns_failure"


@pytest.mark.parametrize(
    "url",
    [
        "http://app.example.com/client.json",
        "ftp://app.example.com/client.json",
        "https://user:pw@app.example.com/client.json",
        "https://app.example.com/client.json#frag",
        "https:///client.json",
        "https://app.example.com:notaport/client.json",
        "",
    ],
)
async def test_only_plain_https_urls_are_fetched(url: str) -> None:
    assert await reason_of(fetcher_for(never_called), url) == "invalid_url"


async def test_the_request_is_pinned_to_the_checked_address_with_the_name_as_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DOCUMENT)

    document = await fetcher_for(handler).fetch(URL + "?v=2")
    assert document == DOCUMENT
    (request,) = seen
    assert request.url.host == PUBLIC_IP  # connects to the address that was checked ...
    assert request.url.path == "/oauth/client.json"
    assert request.url.query == b"v=2"
    assert request.headers["host"] == "app.example.com"  # ... while speaking as the real name
    assert request.extensions["sni_hostname"] == "app.example.com"
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers


async def test_an_ipv6_address_and_a_custom_port_are_formatted_correctly() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DOCUMENT)

    fetcher = fetcher_for(handler, resolver=resolves_to("2606:4700:4700::1111"))
    await fetcher.fetch("https://app.example.com:8443/c.json")
    (request,) = seen
    assert request.url.host == "2606:4700:4700::1111"
    assert request.url.port == 8443
    assert request.headers["host"] == "app.example.com:8443"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_redirects_are_never_followed(status: int) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": "http://169.254.169.254/latest"})

    assert await reason_of(fetcher_for(handler)) == "redirect"
    assert len(calls) == 1  # the Location was not requested


@pytest.mark.parametrize("status", [204, 400, 404, 500])
async def test_only_a_200_is_a_document(status: int) -> None:
    fetcher = fetcher_for(lambda request: httpx.Response(status))
    assert await reason_of(fetcher) == "http_status"


async def test_a_document_over_the_cap_is_refused_by_declared_length() -> None:
    body = b'{"client_id": "' + b"x" * 200 + b'"}'
    fetcher = fetcher_for(lambda request: httpx.Response(200, content=body), max_bytes=100)
    assert await reason_of(fetcher) == "too_large"


async def test_a_document_over_the_cap_is_cut_off_while_streaming_without_a_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        async def chunks():
            for _ in range(1000):
                yield b"x" * 100

        return httpx.Response(200, content=chunks())

    assert await reason_of(fetcher_for(handler, max_bytes=1024)) == "too_large"


async def test_a_document_exactly_at_the_cap_is_accepted() -> None:
    size = len(httpx.Response(200, json=DOCUMENT).content)
    fetcher = fetcher_for(lambda request: httpx.Response(200, json=DOCUMENT), max_bytes=size)
    assert await fetcher.fetch(URL) == DOCUMENT


@pytest.mark.parametrize("body", [b"not json", b"", b"[1, 2]", b'"text"', b"123", b"null"])
async def test_the_answer_must_be_a_json_object(body: bytes) -> None:
    fetcher = fetcher_for(lambda request: httpx.Response(200, content=body))
    assert await reason_of(fetcher) == "not_json"


async def test_transport_errors_are_reported_without_echoing_the_remote() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.1")

    def slow_read(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    network = fetcher_for(refused)
    with pytest.raises(ClientMetadataFetchError) as excinfo:
        await network.fetch(URL)
    assert excinfo.value.reason == "network"
    assert "10.0.0.1" not in str(excinfo.value)
    assert await reason_of(fetcher_for(slow_read)) == "timeout"


async def test_the_whole_fetch_has_a_hard_deadline() -> None:
    async def stalls(host: str, port: int) -> list[str]:
        await asyncio.sleep(5)
        return [PUBLIC_IP]

    fetcher = fetcher_for(never_called, resolver=stalls, timeout_seconds=0.05)
    assert await reason_of(fetcher) == "timeout"
