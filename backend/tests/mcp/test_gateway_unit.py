"""Characterization of `McpGateway` at the raw ASGI level: credential handling, rate limiting,
the body / concurrency caps and the rewritten 421/403 rejections. A stub app, limiter and token
verifier stand in for the real ones, so every branch of `_admit` and `_serve` is pinned, including
the status, JSON body, headers and the structured log event of each rejection."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.testing import capture_logs

from events.rate_limit import RateLimitVerdict
from mcp.auth import TokenVerdict
from mcp.gateway import GatewayConfig, McpGateway

PATH = "/mcp"
METADATA = "https://mcp.example/.well-known/oauth-protected-resource"


class FakeLimiter:
    def __init__(self, verdict: RateLimitVerdict | None = None) -> None:
        self.verdict = verdict or RateLimitVerdict(allowed=True, remaining=1)
        self.calls: list[tuple[str, int, int]] = []

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        self.calls.append((caller_id, max_requests, window_seconds))
        return self.verdict


class FakeVerifier:
    def __init__(self, verdicts: dict[str, TokenVerdict] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.calls: list[str] = []

    async def __call__(self, token: str) -> TokenVerdict:
        self.calls.append(token)
        return self.verdicts.get(token, TokenVerdict(None, "invalid"))


class InnerApp:
    """Records the scope it was handed; answers 200 `ok` unless told otherwise."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": self.status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def make_config(**overrides: object) -> GatewayConfig:
    base: dict[str, object] = {
        "mcp_path": PATH,
        "max_body_bytes": 100,
        "max_concurrent_requests": 2,
        "rate_anonymous_per_minute": 7,
        "rate_signed_in_per_minute": 70,
        "trusted_proxy_count": 0,
        "resource_metadata_url": METADATA,
        "require_auth": True,
        "scope": "mcp:read",
    }
    base.update(overrides)
    return GatewayConfig(**base)  # type: ignore[arg-type]


def make_scope(
    *,
    method: str = "POST",
    path: str = PATH,
    headers: dict[str, str] | list[tuple[str, str]] | None = None,
    client: tuple[str, int] | None = ("9.9.9.9", 1234),
) -> Scope:
    pairs = headers.items() if isinstance(headers, dict) else (headers or [])
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in pairs],
    }
    if client is not None:
        scope["client"] = client
    return scope


class Harness:
    def __init__(
        self,
        *,
        app: ASGIApp | None = None,
        config: GatewayConfig | None = None,
        limiter: FakeLimiter | None = None,
        verifier: FakeVerifier | None = None,
    ) -> None:
        self.inner = app or InnerApp()
        self.limiter = limiter or FakeLimiter()
        self.verifier = verifier or FakeVerifier()
        self.gateway = McpGateway(
            self.inner,
            config=config or make_config(),
            rate_limiter=self.limiter,
            verify_token=self.verifier,
        )

    async def call(
        self, scope: Scope, receive_messages: list[Message] | None = None
    ) -> tuple[list[Message], list[dict[str, object]]]:
        queue = list(
            receive_messages or [{"type": "http.request", "body": b"", "more_body": False}]
        )
        sent: list[Message] = []

        async def receive() -> Message:
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)

        with capture_logs() as logs:
            await self.gateway(scope, receive, send)
        return sent, logs


def status_of(sent: list[Message]) -> int:
    return int(sent[0]["status"])


def headers_of(sent: list[Message]) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}


def json_of(sent: list[Message]) -> dict[str, object]:
    return json.loads(sent[1]["body"])


def inner_called(h: Harness) -> bool:
    return bool(h.inner.scopes)  # type: ignore[attr-defined]


# ----- routing -------------------------------------------------------------------------------


async def test_non_http_scopes_go_straight_to_the_inner_app() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    h = Harness(app=inner)
    await h.gateway({"type": "lifespan"}, lambda: None, lambda m: None)  # type: ignore[arg-type,return-value]

    assert seen == ["lifespan"]


async def test_other_paths_pass_through_without_auth_or_limits() -> None:
    h = Harness()
    sent, logs = await h.call(make_scope(path="/healthz", method="GET"))

    assert status_of(sent) == 200
    assert h.limiter.calls == []
    assert h.verifier.calls == []
    assert logs == []


async def test_trailing_slash_is_rewritten_in_place_and_policed() -> None:
    h = Harness()
    sent, _ = await h.call(make_scope(path=PATH + "/"))

    assert status_of(sent) == 401  # policed like /mcp: no credential
    sent, _ = await h.call(make_scope(path=PATH + "/", headers={"authorization": "Bearer t"}))
    assert status_of(sent) == 401
    h.verifier.verdicts["t"] = TokenVerdict("u1")
    sent, _ = await h.call(make_scope(path=PATH + "/", headers={"authorization": "Bearer t"}))
    assert status_of(sent) == 200
    inner_scope = h.inner.scopes[0]  # type: ignore[attr-defined]
    assert inner_scope["path"] == PATH
    assert inner_scope["raw_path"] == PATH.encode()


async def test_a_double_trailing_slash_is_not_the_mcp_path() -> None:
    h = Harness()
    sent, _ = await h.call(make_scope(path=PATH + "//"))

    assert status_of(sent) == 200
    assert h.limiter.calls == []


async def test_options_preflight_skips_auth_and_rate_limit_but_not_the_body_cap() -> None:
    h = Harness()
    sent, _ = await h.call(make_scope(method="OPTIONS"))

    assert status_of(sent) == 200
    assert h.limiter.calls == []
    assert h.verifier.calls == []

    sent, _ = await h.call(make_scope(method="OPTIONS", headers={"content-length": "101"}))
    assert status_of(sent) == 413


# ----- _admit: credentials -------------------------------------------------------------------


def expected_challenge(
    failure: str | None, *, metadata: bool = True
) -> tuple[dict[str, object], str]:
    parts = ['Bearer realm="miru-mcp"']
    if metadata:
        parts.append(f'resource_metadata="{METADATA}"')
    parts.append('scope="mcp:read"')
    if failure is None:
        body = {"error": "unauthorized", "message": "Sign in to use this server."}
    else:
        description = (
            "The access token has expired; sign in again."
            if failure == "expired"
            else "The access token is invalid; sign in again."
        )
        parts += ['error="invalid_token"', f'error_description="{description}"']
        body = {"error": "invalid_token", "message": description}
    return body, ", ".join(parts)


async def test_no_credential_with_require_auth_gets_the_bare_challenge() -> None:
    h = Harness()
    sent, logs = await h.call(make_scope())

    body, www = expected_challenge(None)
    assert status_of(sent) == 401
    assert json_of(sent) == body
    assert headers_of(sent) == {
        "content-type": "application/json",
        "content-length": str(len(sent[1]["body"])),
        "cache-control": "no-store",
        "www-authenticate": www,
    }
    assert logs == [
        {
            "event": "mcp_unauthorized",
            "log_level": "info",
            "credential_given": False,
            "failure": None,
        }
    ]
    assert h.limiter.calls == []
    assert not inner_called(h)


async def test_challenge_omits_resource_metadata_when_none_is_configured() -> None:
    h = Harness(config=make_config(resource_metadata_url=None))
    sent, _ = await h.call(make_scope())

    assert headers_of(sent)["www-authenticate"] == expected_challenge(None, metadata=False)[1]


async def test_challenge_uses_the_configured_scope() -> None:
    h = Harness(config=make_config(scope="mcp:read mcp:write"))
    sent, _ = await h.call(make_scope())

    assert 'scope="mcp:read mcp:write"' in headers_of(sent)["www-authenticate"]


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer ", "Bearer    ", "Basic abc", "Token abc", "bearerabc", "", "B"],
)
async def test_an_authorization_header_without_a_bearer_token_is_invalid_and_never_verified(
    authorization: str,
) -> None:
    h = Harness()
    sent, logs = await h.call(make_scope(headers={"authorization": authorization}))

    body, www = expected_challenge("invalid")
    assert status_of(sent) == 401
    assert json_of(sent) == body
    assert headers_of(sent)["www-authenticate"] == www
    assert h.verifier.calls == []
    assert logs[0]["credential_given"] is True
    assert logs[0]["failure"] == "invalid"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("invalid", "invalid"), ("expired", "expired"), (None, "invalid")],
)
async def test_a_rejected_token_reports_why(failure: str | None, expected: str) -> None:
    verifier = FakeVerifier({"tok": TokenVerdict(None, failure)})  # type: ignore[arg-type]
    h = Harness(verifier=verifier)
    sent, logs = await h.call(make_scope(headers={"authorization": "Bearer tok"}))

    body, www = expected_challenge(expected)
    assert status_of(sent) == 401
    assert json_of(sent) == body
    assert headers_of(sent)["www-authenticate"] == www
    assert logs[0]["failure"] == expected
    assert not inner_called(h)
    assert h.limiter.calls == []


@pytest.mark.parametrize(
    "authorization", ["Bearer tok", "bearer tok", "BEARER tok", "Bearer   tok  ", "bEaReR tok\t"]
)
async def test_a_valid_bearer_is_stripped_verified_and_rate_limited_per_user(
    authorization: str,
) -> None:
    verifier = FakeVerifier({"tok": TokenVerdict("u1")})
    h = Harness(verifier=verifier)
    sent, logs = await h.call(make_scope(headers={"authorization": authorization}))

    assert status_of(sent) == 200
    assert verifier.calls == ["tok"]
    assert h.limiter.calls == [("mcp:user:u1", 70, 60)]
    assert logs == []


async def test_authorization_wins_over_x_api_key_even_when_it_is_bad() -> None:
    verifier = FakeVerifier({"good": TokenVerdict("u1")})
    h = Harness(verifier=verifier)
    sent, _ = await h.call(make_scope(headers={"authorization": "Bearer bad", "x-api-key": "good"}))

    assert status_of(sent) == 401
    assert verifier.calls == ["bad"]


async def test_x_api_key_is_used_when_sign_in_is_required() -> None:
    verifier = FakeVerifier({"key": TokenVerdict("u2")})
    h = Harness(verifier=verifier)
    sent, _ = await h.call(make_scope(headers={"x-api-key": "  key  "}))

    assert status_of(sent) == 200
    assert verifier.calls == ["key"]
    assert h.limiter.calls == [("mcp:user:u2", 70, 60)]


@pytest.mark.parametrize(
    ("key", "failure"), [("nope", "invalid"), ("old", "expired"), ("", "invalid")]
)
async def test_a_bad_x_api_key_gets_the_matching_challenge(key: str, failure: str) -> None:
    verifier = FakeVerifier({"old": TokenVerdict(None, "expired")})
    h = Harness(verifier=verifier)
    sent, logs = await h.call(make_scope(headers={"x-api-key": key}))

    body, www = expected_challenge(failure)
    assert status_of(sent) == 401
    assert json_of(sent) == body
    assert headers_of(sent)["www-authenticate"] == www
    assert verifier.calls == [key.strip()]  # even an empty key is handed to the verifier
    assert logs[0]["credential_given"] is True


async def test_x_api_key_is_ignored_when_sign_in_is_not_required() -> None:
    verifier = FakeVerifier({"key": TokenVerdict("u2")})
    h = Harness(config=make_config(require_auth=False), verifier=verifier)
    sent, _ = await h.call(make_scope(headers={"x-api-key": "key"}))

    assert status_of(sent) == 200
    assert verifier.calls == []
    assert h.limiter.calls == [("mcp:ip:9.9.9.9", 7, 60)]


async def test_without_require_auth_a_bad_bearer_is_still_refused() -> None:
    h = Harness(config=make_config(require_auth=False))
    sent, _ = await h.call(make_scope(headers={"authorization": "Bearer bad"}))

    assert status_of(sent) == 401
    assert h.limiter.calls == []


async def test_without_require_auth_a_valid_bearer_is_signed_in() -> None:
    verifier = FakeVerifier({"tok": TokenVerdict("u9")})
    h = Harness(config=make_config(require_auth=False), verifier=verifier)
    sent, _ = await h.call(make_scope(headers={"authorization": "Bearer tok"}))

    assert status_of(sent) == 200
    assert h.limiter.calls == [("mcp:user:u9", 70, 60)]


# ----- _admit: anonymous callers and the client address -------------------------------------


def anon(**overrides: object) -> GatewayConfig:
    return make_config(require_auth=False, **overrides)


@pytest.mark.parametrize(
    ("trusted", "forwarded", "client", "expected"),
    [
        (0, ["1.1.1.1"], ("9.9.9.9", 1), "9.9.9.9"),
        (0, [], ("9.9.9.9", 1), "9.9.9.9"),
        (0, [], None, "unknown"),
        (1, ["1.1.1.1, 2.2.2.2"], ("9.9.9.9", 1), "2.2.2.2"),
        (2, ["1.1.1.1, 2.2.2.2, 3.3.3.3"], ("9.9.9.9", 1), "2.2.2.2"),
        (3, ["1.1.1.1, 2.2.2.2"], ("9.9.9.9", 1), "9.9.9.9"),
        (1, [], ("9.9.9.9", 1), "9.9.9.9"),
        (1, ["not-an-ip"], ("9.9.9.9", 1), "9.9.9.9"),
        (1, ["1.1.1.1, junk"], ("9.9.9.9", 1), "9.9.9.9"),
        (1, ["1.1.1.1", "2.2.2.2"], ("9.9.9.9", 1), "2.2.2.2"),
        (2, ["1.1.1.1", "2.2.2.2"], ("9.9.9.9", 1), "1.1.1.1"),
        (1, [" , 1.1.1.1 ,, "], ("9.9.9.9", 1), "1.1.1.1"),
        (1, ["2001:DB8:0:0:0:0:0:1"], ("9.9.9.9", 1), "2001:db8::1"),
        (1, ["1.1.1.1"], None, "1.1.1.1"),
        (1, ["junk"], None, "unknown"),
    ],
)
async def test_anonymous_callers_are_keyed_by_the_trusted_hop_or_the_socket_peer(
    trusted: int,
    forwarded: list[str],
    client: tuple[str, int] | None,
    expected: str,
) -> None:
    h = Harness(config=anon(trusted_proxy_count=trusted))
    pairs = [("x-forwarded-for", v) for v in forwarded]
    sent, _ = await h.call(make_scope(headers=pairs, client=client))

    assert status_of(sent) == 200
    assert h.limiter.calls == [(f"mcp:ip:{expected}", 7, 60)]


async def test_a_negative_proxy_count_ignores_the_header() -> None:
    h = Harness(config=anon(trusted_proxy_count=-1))
    await h.call(make_scope(headers={"x-forwarded-for": "1.1.1.1"}))

    assert h.limiter.calls == [("mcp:ip:9.9.9.9", 7, 60)]


# ----- _admit: rate limit --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [(None, 60), (0, 60), (0.2, 1), (1.0, 1), (30.7, 30), (59.99, 59), (120, 120), (-5, 1)],
)
async def test_a_rate_limited_caller_gets_429_with_retry_after(
    retry_after: float | None, expected: int
) -> None:
    limiter = FakeLimiter(
        RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=retry_after)
    )
    verifier = FakeVerifier({"tok": TokenVerdict("u1")})
    h = Harness(limiter=limiter, verifier=verifier)
    sent, logs = await h.call(make_scope(headers={"authorization": "Bearer tok"}))

    assert status_of(sent) == 429
    assert json_of(sent) == {
        "error": "rate_limited",
        "message": f"Too many requests; retry in {expected} seconds.",
    }
    assert headers_of(sent)["retry-after"] == str(expected)
    assert headers_of(sent)["cache-control"] == "no-store"
    assert logs == [
        {
            "event": "mcp_rate_limited",
            "log_level": "warning",
            "caller_kind": "user",
            "limit_per_minute": 70,
        }
    ]
    assert not inner_called(h)


async def test_a_rate_limited_anonymous_caller_is_logged_as_ip() -> None:
    limiter = FakeLimiter(RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=5))
    h = Harness(config=anon(), limiter=limiter)
    sent, logs = await h.call(make_scope())

    assert status_of(sent) == 429
    assert logs[0]["caller_kind"] == "ip"
    assert logs[0]["limit_per_minute"] == 7


# ----- _serve: body cap ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "status"), [("100", 200), ("101", 413), ("0", 200), ("99999999999999999999", 413)]
)
async def test_a_declared_content_length_over_the_cap_is_413(declared: str, status: int) -> None:
    h = Harness(config=anon())
    sent, _ = await h.call(make_scope(headers={"content-length": declared}))

    assert status_of(sent) == status
    if status == 413:
        assert json_of(sent) == {
            "error": "request_too_large",
            "message": "Request body exceeds 100 bytes.",
        }
        assert not inner_called(h)


@pytest.mark.parametrize("declared", ["abc", "-1", "", "1e3", "5 00", "0x200"])
async def test_a_non_digit_content_length_is_not_trusted_either_way(declared: str) -> None:
    h = Harness(config=anon())
    sent, _ = await h.call(make_scope(headers={"content-length": declared}))

    assert status_of(sent) == 200


async def test_the_body_cap_is_checked_before_the_in_flight_cap() -> None:
    h = Harness(config=anon(max_concurrent_requests=0))
    sent, _ = await h.call(make_scope(headers={"content-length": "101"}))

    assert status_of(sent) == 413


def chunk(size: int, *, more: bool = True) -> Message:
    return {"type": "http.request", "body": b"x" * size, "more_body": more}


class Reader:
    """An inner app that reads every request message, then answers like `InnerApp`."""

    def __init__(self, *, respond_first: bool = False, status: int = 200) -> None:
        self.respond_first = respond_first
        self.status = status
        self.received: list[Message] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.respond_first:
            await send({"type": "http.response.start", "status": self.status, "headers": []})
        while True:
            message = await receive()
            self.received.append(message)
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        if not self.respond_first:
            await send({"type": "http.response.start", "status": self.status, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})


async def test_chunks_up_to_the_cap_are_delivered() -> None:
    reader = Reader()
    h = Harness(app=reader, config=anon())
    sent, _ = await h.call(make_scope(), [chunk(60), chunk(40, more=False)])

    assert status_of(sent) == 200
    assert [len(m["body"]) for m in reader.received] == [60, 40]


async def test_a_streamed_body_over_the_cap_becomes_413_when_nothing_was_sent_yet() -> None:
    reader = Reader()
    h = Harness(app=reader, config=anon())
    sent, _ = await h.call(make_scope(), [chunk(60), chunk(41, more=False)])

    assert status_of(sent) == 413
    assert json_of(sent)["error"] == "request_too_large"
    assert len(sent) == 2
    assert len(reader.received) == 1


async def test_a_streamed_body_over_the_cap_after_the_response_started_is_dropped_quietly() -> None:
    reader = Reader(respond_first=True)
    h = Harness(app=reader, config=anon())
    sent, _ = await h.call(make_scope(), [chunk(60), chunk(60, more=False)])

    assert [m["type"] for m in sent] == ["http.response.start"]
    assert status_of(sent) == 200


async def test_a_streamed_body_over_the_cap_after_a_rewritten_rejection_adds_nothing() -> None:
    reader = Reader(respond_first=True, status=421)
    h = Harness(app=reader, config=anon())
    sent, _ = await h.call(make_scope(headers={"host": "evil.example"}), [chunk(60), chunk(60)])

    assert status_of(sent) == 421
    assert len(sent) == 2  # the explanation only; no 413 on top


async def test_non_request_messages_do_not_count_toward_the_cap() -> None:
    reader = Reader()
    h = Harness(app=reader, config=anon())
    sent, _ = await h.call(make_scope(), [{"type": "http.disconnect", "body": b"x" * 500}])

    assert status_of(sent) == 200


# ----- _serve: concurrency cap ---------------------------------------------------------------


class Parked:
    """An inner app that parks until released."""

    def __init__(self) -> None:
        self.entered = 0
        self.release = asyncio.Event()
        self.arrived = asyncio.Event()
        self._target = 1
        self._reached = asyncio.Event()

    async def wait_entered(self, count: int) -> None:
        self._target = count
        if self.entered < count:
            await self._reached.wait()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.entered += 1
        self.arrived.set()
        if self.entered >= self._target:
            self._reached.set()
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def test_posts_beyond_the_cap_are_shed_with_503_and_the_slot_is_released() -> None:
    parked = Parked()
    h = Harness(app=parked, config=anon(max_concurrent_requests=1))
    first = asyncio.create_task(h.call(make_scope()))
    await parked.arrived.wait()
    assert h.gateway._in_flight == 1

    sent, logs = await h.call(make_scope())

    assert status_of(sent) == 503
    assert json_of(sent) == {"error": "busy", "message": "Server is busy; retry shortly."}
    assert headers_of(sent)["retry-after"] == "1"
    assert logs == [{"event": "mcp_concurrency_cap_hit", "log_level": "warning", "cap": 1}]
    assert parked.entered == 1
    assert h.gateway._in_flight == 1  # a shed request never took a slot

    parked.release.set()
    done, _ = await first
    assert status_of(done) == 200
    assert h.gateway._in_flight == 0

    parked.arrived.clear()
    again = await h.call(make_scope())
    assert status_of(again[0]) == 200


async def test_a_get_stream_is_never_counted_or_shed() -> None:
    parked = Parked()
    h = Harness(app=parked, config=anon(max_concurrent_requests=1))
    tasks = [asyncio.create_task(h.call(make_scope(method="GET"))) for _ in range(3)]
    await parked.wait_entered(3)
    assert h.gateway._in_flight == 0

    parked.release.set()
    results = await asyncio.gather(*tasks)
    assert [status_of(r[0]) for r in results] == [200, 200, 200]


async def test_the_slot_is_released_when_the_app_raises() -> None:
    class BoomError(Exception):
        pass

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        raise BoomError

    h = Harness(app=inner, config=anon())
    with pytest.raises(BoomError):
        await h.call(make_scope())

    assert h.gateway._in_flight == 0


async def test_the_cap_boundary_admits_exactly_that_many() -> None:
    parked = Parked()
    h = Harness(app=parked, config=anon(max_concurrent_requests=2))
    a = asyncio.create_task(h.call(make_scope()))
    b = asyncio.create_task(h.call(make_scope()))
    await parked.wait_entered(2)

    shed, _ = await h.call(make_scope())
    assert status_of(shed) == 503

    parked.release.set()
    await asyncio.gather(a, b)


# ----- _serve: rejected Host / Origin --------------------------------------------------------


async def test_a_421_from_the_app_is_replaced_by_a_message_naming_the_host() -> None:
    h = Harness(app=InnerApp(status=421), config=anon())
    sent, logs = await h.call(make_scope(headers={"host": "evil.example"}))

    assert status_of(sent) == 421
    assert json_of(sent) == {
        "error": "host_not_allowed",
        "message": (
            "Host 'evil.example' is not allowed. Set MCP_PUBLIC_BASE_URL to this server's "
            "public https URL (or add the hostname to MCP_ALLOWED_HOSTS) and restart."
        ),
    }
    assert len(sent) == 2  # the app's own body is swallowed
    assert headers_of(sent)["cache-control"] == "no-store"
    assert logs == [{"event": "mcp_host_rejected", "log_level": "warning", "host": "evil.example"}]


async def test_a_421_without_a_host_header_names_an_empty_host() -> None:
    h = Harness(app=InnerApp(status=421), config=anon())
    sent, logs = await h.call(make_scope())

    assert "Host '' is not allowed" in str(json_of(sent)["message"])
    assert logs[0]["host"] == ""


async def test_a_403_from_the_app_is_replaced_by_the_origin_message() -> None:
    h = Harness(app=InnerApp(status=403), config=anon())
    sent, logs = await h.call(make_scope(headers={"host": "ok.example"}))

    assert status_of(sent) == 403
    assert json_of(sent) == {
        "error": "origin_not_allowed",
        "message": (
            "The Origin header is not allowed. Add it to MCP_ALLOWED_ORIGINS "
            "(browser-based clients only; server-side connectors send no Origin)."
        ),
    }
    assert len(sent) == 2
    assert logs == [{"event": "mcp_origin_rejected", "log_level": "warning"}]


@pytest.mark.parametrize("status", [200, 202, 400, 401, 404, 405, 415, 500])
async def test_every_other_status_passes_through_untouched(status: int) -> None:
    h = Harness(app=InnerApp(status=status), config=anon())
    sent, logs = await h.call(make_scope())

    assert sent == [
        {"type": "http.response.start", "status": status, "headers": []},
        {"type": "http.response.body", "body": b"ok"},
    ]
    assert logs == []


async def test_rejection_rewrite_applies_to_get_and_options_too() -> None:
    for method in ("GET", "OPTIONS"):
        h = Harness(app=InnerApp(status=403), config=anon())
        sent, _ = await h.call(make_scope(method=method))
        assert json_of(sent)["error"] == "origin_not_allowed"
