"""Characterization of `ConditionalGetMiddleware` at the raw ASGI message level: eligibility,
buffering, header rewriting and the 304 shape. No HTTP client in between, so the exact messages
the middleware emits are pinned."""

from __future__ import annotations

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.etag import ConditionalGetMiddleware, _matches, etag_for

Headers = list[tuple[bytes, bytes]]


async def _noop_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope(*, method: str = "GET", headers: Headers | None = None, kind: str = "http") -> Scope:
    return {"type": kind, "method": method, "headers": headers or [], "path": "/"}


def _app(messages: list[Message]) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        for message in messages:
            await send(message)

    return app


async def _drive(app: ASGIApp, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await ConditionalGetMiddleware(app)(scope, _noop_receive, send)
    return sent


def _start(status: int = 200, headers: Headers | None = None) -> Message:
    return {
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")] if headers is None else headers,
    }


def _body(body: bytes, *, more: bool | None = None) -> Message:
    message: Message = {"type": "http.response.body", "body": body}
    if more is not None:
        message["more_body"] = more
    return message


def _hdr(message: Message) -> dict[bytes, bytes]:
    return dict(message["headers"])


BODY = b'{"n":1}'
TAG = etag_for(BODY)


async def test_tagged_response_exact_messages() -> None:
    sent = await _drive(
        _app(
            [_start(headers=[(b"content-type", b"application/json"), (b"x-a", b"1")]), _body(BODY)]
        ),
        _scope(),
    )

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 200
    assert sent[0]["headers"] == [
        (b"content-type", b"application/json"),
        (b"x-a", b"1"),
        (b"etag", TAG.encode()),
        (b"cache-control", b"private, no-cache"),
        (b"vary", b"Authorization, Cookie"),
    ]
    assert sent[1] == {"type": "http.response.body", "body": BODY}


async def test_existing_cache_control_is_kept_and_vary_is_still_appended() -> None:
    sent = await _drive(
        _app(
            [
                _start(
                    headers=[
                        (b"Content-Type", b"application/json"),
                        (b"Cache-Control", b"max-age=5"),
                        (b"Vary", b"Accept"),
                    ]
                ),
                _body(BODY),
            ]
        ),
        _scope(),
    )

    assert sent[0]["headers"] == [
        (b"Content-Type", b"application/json"),
        (b"Cache-Control", b"max-age=5"),
        (b"Vary", b"Accept"),
        (b"etag", TAG.encode()),
        (b"vary", b"Authorization, Cookie"),
    ]


async def test_content_type_with_charset_is_eligible_and_name_case_is_ignored() -> None:
    sent = await _drive(
        _app(
            [_start(headers=[(b"CONTENT-TYPE", b"application/json; charset=utf-8")]), _body(BODY)]
        ),
        _scope(),
    )

    assert b"etag" in _hdr(sent[0])


async def test_chunked_body_is_buffered_and_joined() -> None:
    sent = await _drive(
        _app([_start(), _body(b'{"n"', more=True), _body(b":1}", more=False)]), _scope()
    )

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[1]["body"] == BODY
    assert _hdr(sent[0])[b"etag"] == TAG.encode()


async def test_empty_body_is_tagged() -> None:
    sent = await _drive(_app([_start(), _body(b"")]), _scope())

    assert _hdr(sent[0])[b"etag"] == etag_for(b"").encode()
    assert sent[1]["body"] == b""


async def test_body_message_without_body_key_counts_as_empty() -> None:
    sent = await _drive(_app([_start(), {"type": "http.response.body"}]), _scope())

    assert sent[1] == {"type": "http.response.body", "body": b""}


async def test_start_without_headers_key_is_not_json_so_it_passes_through() -> None:
    start: Message = {"type": "http.response.start", "status": 200}
    sent = await _drive(_app([start, _body(BODY)]), _scope())

    assert sent == [start, _body(BODY)]


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-type", b"text/html")],
        [(b"content-type", b"text/event-stream")],
        [(b"content-type", b"x-application/json")],
        [(b"content-length", b"7")],
        [(b"content-type", b"application/json"), (b"set-cookie", b"a=b")],
        [(b"content-type", b"application/json"), (b"Set-Cookie", b"a=b")],
        [(b"content-type", b"application/json"), (b"etag", b'"x"')],
        [(b"content-type", b"application/json"), (b"ETag", b'"x"')],
    ],
)
async def test_ineligible_headers_pass_through_unchanged(headers: Headers) -> None:
    start = _start(headers=headers)
    sent = await _drive(_app([start, _body(BODY, more=True), _body(b"x")]), _scope())

    assert sent == [start, _body(BODY, more=True), _body(b"x")]


@pytest.mark.parametrize("status", [201, 204, 301, 304, 400, 404, 500])
async def test_non_200_passes_through_even_if_the_client_has_the_tag(status: int) -> None:
    start = _start(status=status)
    scope = _scope(headers=[(b"if-none-match", b"*")])
    sent = await _drive(_app([start, _body(BODY)]), scope)

    assert sent == [start, _body(BODY)]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def test_non_get_methods_pass_through(method: str) -> None:
    messages = [_start(), _body(BODY)]
    sent = await _drive(_app(messages), _scope(method=method))

    assert sent == messages


@pytest.mark.parametrize("kind", ["websocket", "lifespan"])
async def test_non_http_scopes_are_handed_straight_to_the_app(kind: str) -> None:
    seen: list[tuple[Scope, Receive, Send]] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append((scope, receive, send))

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": kind}  # no "method" key: must not be read
    await ConditionalGetMiddleware(app)(scope, _noop_receive, send)

    assert seen == [(scope, _noop_receive, send)]


async def test_body_without_start_is_forwarded_as_is() -> None:
    sent = await _drive(_app([_body(BODY)]), _scope())

    assert sent == [_body(BODY)]


async def test_passthrough_decision_sticks_for_later_messages() -> None:
    messages = [_start(status=404), _body(b"a", more=True), _body(b"b", more=False)]
    sent = await _drive(_app(messages), _scope())

    assert sent == messages


async def test_matching_tag_answers_304_dropping_length_and_type() -> None:
    start = _start(
        headers=[
            (b"content-type", b"application/json"),
            (b"Content-Length", b"7"),
            (b"x-keep", b"1"),
        ]
    )
    sent = await _drive(
        _app([start, _body(BODY)]), _scope(headers=[(b"if-none-match", TAG.encode())])
    )

    assert sent[0]["status"] == 304
    assert sent[0]["headers"] == [
        (b"x-keep", b"1"),
        (b"etag", TAG.encode()),
        (b"cache-control", b"private, no-cache"),
        (b"vary", b"Authorization, Cookie"),
    ]
    assert sent[1] == {"type": "http.response.body", "body": b""}
    assert len(sent) == 2


async def test_304_keeps_other_start_message_keys() -> None:
    start = {**_start(), "trailers": True}
    sent = await _drive(_app([start, _body(BODY)]), _scope(headers=[(b"if-none-match", b"*")]))

    assert sent[0]["trailers"] is True
    assert sent[0]["status"] == 304


@pytest.mark.parametrize(
    "value",
    [
        TAG,
        TAG.removeprefix("W/"),
        f'"nope", {TAG}',
        f'W/"nope",{TAG}',
        f"  {TAG}  ",
        "*",
        '"a", *',
    ],
)
async def test_matching_forms_give_304(value: str) -> None:
    sent = await _drive(
        _app([_start(), _body(BODY)]), _scope(headers=[(b"if-none-match", value.encode())])
    )

    assert sent[0]["status"] == 304


@pytest.mark.parametrize("value", ["", '"nope"', 'W/"nope"', f"{TAG}x", ",", "x*"])
async def test_non_matching_if_none_match_gets_the_full_body(value: str) -> None:
    sent = await _drive(
        _app(
            [
                _start(headers=[(b"content-type", b"application/json"), (b"content-length", b"7")]),
                _body(BODY),
            ]
        ),
        _scope(headers=[(b"if-none-match", value.encode())]),
    )

    assert sent[0]["status"] == 200
    assert (b"content-length", b"7") in sent[0]["headers"]
    assert sent[1]["body"] == BODY


async def test_last_if_none_match_header_wins() -> None:
    scope = _scope(headers=[(b"if-none-match", TAG.encode()), (b"If-None-Match", b'"other"')])
    # header names in ASGI scopes are lowercase; the second one here is not matched at all
    sent = await _drive(_app([_start(), _body(BODY)]), scope)
    assert sent[0]["status"] == 304

    scope = _scope(headers=[(b"if-none-match", TAG.encode()), (b"if-none-match", b'"other"')])
    sent = await _drive(_app([_start(), _body(BODY)]), scope)
    assert sent[0]["status"] == 200


async def test_if_none_match_is_decoded_as_latin_1() -> None:
    scope = _scope(headers=[(b"if-none-match", b"\xe9, " + TAG.encode())])
    sent = await _drive(_app([_start(), _body(BODY)]), scope)

    assert sent[0]["status"] == 304


@pytest.mark.parametrize(
    ("header", "etag", "expected"),
    [
        ('W/"a"', 'W/"a"', True),
        ('"a"', 'W/"a"', True),
        ('W/"a"', '"a"', True),
        ('"b", W/"a"', 'W/"a"', True),
        ('"b" ,  "a"', 'W/"a"', True),
        ("*", 'W/"a"', True),
        ('"b", *', 'W/"a"', True),
        ('"b"', 'W/"a"', False),
        ("", 'W/"a"', False),
        (",", 'W/"a"', False),
        ('W/ "a"', 'W/"a"', False),
        ('w/"a"', 'W/"a"', False),
    ],
)
def test_matches_weak_comparison(header: str, etag: str, expected: bool) -> None:
    assert _matches(header, etag) is expected
