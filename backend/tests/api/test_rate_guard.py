"""Public data reads and sign-in endpoints are rate limited per caller, answering 429 in the
standard envelope with a `Retry-After`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from fastapi import Request

from api.rate_guard import client_address
from config.settings import Settings
from events.rate_limit import RateLimitVerdict
from tests.api.conftest import build_test_client


class _Recorder:
    """A limiter that denies once a caller has made `allow` requests, remembering who asked."""

    def __init__(self, allow: int) -> None:
        self.allow = allow
        self.counts: dict[str, int] = {}

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        del max_requests, window_seconds
        self.counts[caller_id] = self.counts.get(caller_id, 0) + 1
        if self.counts[caller_id] > self.allow:
            return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=17.2)
        return RateLimitVerdict(allowed=True, remaining=1)


def test_a_data_route_answers_429_with_the_envelope_once_the_caller_is_over(
    settings: Settings,
) -> None:
    limiter = _Recorder(allow=1)
    with build_test_client(settings, agent_answer="x", rate_limiter=limiter) as client:
        first = client.get("/api/v1/market/index-strip")
        second = client.get("/api/v1/market/index-strip")

    assert first.status_code != 429
    assert second.status_code == 429
    assert second.json() == {
        "code": "rate_limited",
        "message": "Too many requests. Please slow down and retry shortly.",
        "retryable": True,
    }
    assert second.headers["retry-after"] == "17"
    assert any(caller.startswith("read:ip:") for caller in limiter.counts)


def test_sign_in_endpoints_are_limited_by_address(settings: Settings) -> None:
    limiter = _Recorder(allow=0)
    with build_test_client(settings, agent_answer="x", rate_limiter=limiter) as client:
        response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 429
    assert any(caller.startswith("auth:ip:") for caller in limiter.counts)


def test_routes_outside_the_guarded_set_are_not_counted(settings: Settings) -> None:
    limiter = _Recorder(allow=0)
    with build_test_client(settings, agent_answer="x", rate_limiter=limiter) as client:
        response = client.get("/healthz")

    assert response.status_code != 429
    assert limiter.counts == {}


def _request(forwarded: str | None, host: str = "10.0.0.1") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return cast(
        Request,
        SimpleNamespace(
            headers={k.decode(): v.decode() for k, v in headers},
            client=SimpleNamespace(host=host),
        ),
    )


def test_forwarded_for_is_ignored_without_a_trusted_proxy() -> None:
    request = _request("203.0.113.9")

    assert client_address(request, trusted_proxy_count=0) == "10.0.0.1"


def test_the_caller_is_counted_from_the_right_so_a_forged_prefix_is_ignored() -> None:
    # The client sent "6.6.6.6"; our one proxy appended the address it actually saw.
    forged = _request("6.6.6.6, 203.0.113.9")
    # Two proxies of ours: the client, then proxy 1's peer, appended by proxy 2.
    two_hops = _request("6.6.6.6, 203.0.113.9, 10.1.1.1")

    assert client_address(forged, trusted_proxy_count=1) == "203.0.113.9"
    assert client_address(two_hops, trusted_proxy_count=2) == "203.0.113.9"


def test_a_short_or_garbage_header_falls_back_to_the_socket_peer() -> None:
    assert client_address(_request("203.0.113.9"), trusted_proxy_count=2) == "10.0.0.1"
    assert client_address(_request("not-an-ip"), trusted_proxy_count=1) == "10.0.0.1"
    assert client_address(_request(None), trusted_proxy_count=1) == "10.0.0.1"
