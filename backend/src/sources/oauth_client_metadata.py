"""Fetch an OAuth Client ID Metadata Document - the one place the authorization server reaches out.

A client (Claude Code, for one) may use an https URL as its `client_id`; the authorization server
then downloads the JSON document at that URL to learn the client's name and redirect URIs. The URL
is chosen by whoever sends the authorize request, so this is a server-side request forgery
primitive unless it is fenced in. Every rule below exists for that:

- https only, no userinfo, no fragment; nothing but the document's own host is ever contacted.
- The host is resolved here and EVERY address it resolves to must be globally routable - a private,
  loopback, link-local (cloud metadata is 169.254.169.254), CGNAT, reserved, multicast or
  NAT64/6to4-embedded address rejects the whole fetch.
- The connection is then made to the address that was checked (the request goes to the IP, with the
  original name as `Host` and TLS server name), so a name that answers differently the second time
  (DNS rebinding) cannot redirect the request.
- Redirects are never followed; the answer is a single `200` with a JSON object.
- A hard timeout and a response-size cap (the body is read as a bounded stream).

`resolver` and `client_factory` exist so tests can supply a deterministic name service and a
transport; production uses the system resolver and the shared strict-TLS client.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx
import structlog

from schemas.enums import TLSPolicy
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

FetchFailure = Literal[
    "invalid_url",
    "blocked_address",
    "dns_failure",
    "redirect",
    "http_status",
    "too_large",
    "not_json",
    "timeout",
    "network",
]

type Resolver = Callable[[str, int], Awaitable[list[str]]]
type ClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]

_HTTPS_PORT = 443
_HTTP_OK = 200
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_USER_AGENT = "MiruAuthorizationServer/1 (+client-metadata-fetch)"


class ClientMetadataFetchError(Exception):
    """The document could not be fetched. `reason` is a stable code; the text never echoes the
    remote body."""

    def __init__(self, reason: FetchFailure, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason: FetchFailure = reason
        self.detail = detail


def is_public_address(address: str) -> bool:
    """True only for a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(str(ip.ipv4_mapped))
        if ip in _NAT64 or ip in _SIX_TO_FOUR:
            return False
    return ip.is_global and not ip.is_multicast


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _ip_literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


class ClientMetadataFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_bytes: int = 65_536,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        resolver: Resolver | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._resolver: Resolver = resolver or _system_resolver
        self._client_factory: ClientFactory = client_factory or (
            lambda: build_async_client(tls_policy=tls_policy, timeout=timeout_seconds)
        )

    async def fetch(self, url: str) -> dict[str, object]:
        host, port, path_and_query = self._parse(url)
        try:
            async with asyncio.timeout(self._timeout):
                address = await self._checked_address(host, port)
                return await self._get(host, port, address, path_and_query)
        except TimeoutError as exc:
            raise ClientMetadataFetchError("timeout") from exc

    @staticmethod
    def _parse(url: str) -> tuple[str, int, str]:
        try:
            parts = urlsplit(url)
            port = parts.port or _HTTPS_PORT
            host = parts.hostname
        except ValueError as exc:
            raise ClientMetadataFetchError("invalid_url") from exc
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise ClientMetadataFetchError("invalid_url", "must be an https URL without userinfo")
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        return host, port, target

    async def _checked_address(self, host: str, port: int) -> str:
        literal = _ip_literal(host)
        try:
            addresses = [literal] if literal else await self._resolver(host, port)
        except OSError as exc:
            raise ClientMetadataFetchError("dns_failure") from exc
        if not addresses:
            raise ClientMetadataFetchError("dns_failure")
        blocked = [a for a in addresses if not is_public_address(a)]
        if blocked:
            logger.warning("oauth_client_metadata_blocked", host=host, blocked=len(blocked))
            raise ClientMetadataFetchError("blocked_address")
        return addresses[0]

    async def _get(self, host: str, port: int, address: str, target: str) -> dict[str, object]:
        netloc_ip = f"[{address}]" if ":" in address else address
        host_header = host if port == _HTTPS_PORT else f"{host}:{port}"
        request_url = f"https://{netloc_ip}:{port}{target}"
        try:
            async with (
                self._client_factory() as client,
                client.stream(
                    "GET",
                    request_url,
                    headers={
                        "Host": host_header,
                        "Accept": "application/json",
                        "User-Agent": _USER_AGENT,
                    },
                    extensions={"sni_hostname": host},
                    follow_redirects=False,
                ) as response,
            ):
                return await self._read(response)
        except httpx.TimeoutException as exc:
            raise ClientMetadataFetchError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ClientMetadataFetchError("network", type(exc).__name__) from exc

    async def _read(self, response: httpx.Response) -> dict[str, object]:
        if 300 <= response.status_code < 400:  # noqa: PLR2004 - the 3xx class
            raise ClientMetadataFetchError("redirect")
        if response.status_code != _HTTP_OK:
            raise ClientMetadataFetchError("http_status", str(response.status_code))
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            raise ClientMetadataFetchError("too_large")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._max_bytes:
                raise ClientMetadataFetchError("too_large")
        try:
            document = json.loads(bytes(body))
        except ValueError as exc:
            raise ClientMetadataFetchError("not_json") from exc
        if not isinstance(document, dict):
            raise ClientMetadataFetchError("not_json", "top-level value is not an object")
        return {str(key): value for key, value in cast("dict[object, object]", document).items()}


__all__ = [
    "ClientMetadataFetchError",
    "ClientMetadataFetcher",
    "FetchFailure",
    "is_public_address",
]
