"""Hardened page fetch: SSRF defences on every hop, size cap, content-type allowlist."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

import sources.web_search as web_search_module
from config.settings import Settings
from schemas.source import HealthStatus
from sources.web_search import WebSearchAdapter

pytestmark = pytest.mark.governance

PUBLIC_IP = "93.184.216.34"


async def _public(_host: str, _port: int) -> list[str]:
    return [PUBLIC_IP]


def _serve(
    monkeypatch: pytest.MonkeyPatch, responder: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responder(request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(web_search_module, "build_async_client", factory)
    return seen


def _adapter() -> WebSearchAdapter:
    return WebSearchAdapter(settings=Settings())


_HTML = (
    "<html><head><title>TSMC Monthly Revenue</title><script>evil()</script></head>"
    "<body><nav>menu</nav><p>Revenue rose 12%.</p><footer>copyright</footer></body></html>"
)


async def test_html_is_reduced_to_readable_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(
        monkeypatch,
        lambda _r: httpx.Response(
            200, text=_HTML, headers={"content-type": "text/html; charset=utf-8"}
        ),
    )
    result = await _adapter().fetch_page("https://news.example.com/a", resolver=_public)

    assert result.health.status is HealthStatus.OK
    assert result.payload["title"] == "TSMC Monthly Revenue"
    assert result.payload["text"] == "Revenue rose 12%."
    # the request went to the checked IP, with the real host kept for Host/SNI
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "news.example.com"
    assert seen[0].extensions["sni_hostname"] == "news.example.com"


@pytest.mark.parametrize(
    "url",
    ["http://localhost/", "http://10.0.0.1/", "ftp://a.example.com/", "https://u:p@a.example.com/"],
)
async def test_unsafe_urls_are_never_requested(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, text="x"))
    result = await _adapter().fetch_page(url, resolver=_public)
    assert result.health.status is HealthStatus.UNREACHABLE
    assert seen == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fd00::1"])
async def test_a_public_name_resolving_to_an_internal_address_is_blocked(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, text="secret"))

    async def rebinding(_host: str, _port: int) -> list[str]:
        return [PUBLIC_IP, address]  # one bad answer poisons the lot

    result = await _adapter().fetch_page("https://evil.example.com/", resolver=rebinding)
    assert result.health.status is HealthStatus.UNREACHABLE
    assert seen == []


async def test_every_redirect_hop_is_revalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "ok.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
        return httpx.Response(200, text="metadata")

    seen = _serve(monkeypatch, responder)
    result = await _adapter().fetch_page("https://ok.example.com/", resolver=_public)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert len(seen) == 1  # the internal hop was never requested


async def test_a_redirect_to_another_public_page_is_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new?utm_source=x"})
        return httpx.Response(200, text="moved here", headers={"content-type": "text/plain"})

    _serve(monkeypatch, responder)
    result = await _adapter().fetch_page("https://a.example.com/old", resolver=_public)

    assert result.payload["url"] == "https://a.example.com/new"
    assert result.payload["text"] == "moved here"


async def test_redirect_loops_are_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(302, headers={"location": "/again"}))
    result = await _adapter().fetch_page("https://a.example.com/", resolver=_public)
    assert result.health.status is HealthStatus.UNREACHABLE
    assert len(seen) == 5


async def test_binary_content_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(
        monkeypatch,
        lambda _r: httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        ),
    )
    result = await _adapter().fetch_page("https://a.example.com/x.pdf", resolver=_public)
    assert result.health.status is HealthStatus.UNREACHABLE


async def test_oversized_bodies_are_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    big = "a" * (web_search_module._MAX_PAGE_BYTES + 5000)
    _serve(
        monkeypatch,
        lambda _r: httpx.Response(200, text=big, headers={"content-type": "text/plain"}),
    )
    result = await _adapter().fetch_page("https://a.example.com/", resolver=_public)
    text = result.payload["text"]
    assert isinstance(text, str)
    assert len(text) == web_search_module._MAX_PAGE_BYTES


async def test_http_errors_and_dns_failures_are_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(404, text="no"))
    missing = await _adapter().fetch_page("https://a.example.com/", resolver=_public)
    assert missing.health.status is HealthStatus.UNREACHABLE

    async def no_dns(_host: str, _port: int) -> list[str]:
        raise OSError("nxdomain")

    failed = await _adapter().fetch_page("https://a.example.com/", resolver=no_dns)
    assert failed.health.status is HealthStatus.UNREACHABLE


async def test_compressed_bodies_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(
        monkeypatch,
        lambda _r: httpx.Response(
            200, content=b"x", headers={"content-type": "text/html", "content-encoding": "gzip"}
        ),
    )
    result = await _adapter().fetch_page("https://a.example.com/", resolver=_public)
    assert result.health.status is HealthStatus.UNREACHABLE
    assert seen[0].headers["accept-encoding"] == "identity"


async def test_a_rejected_response_body_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tripwire(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("body was read before the headers were vetted")
            yield b""

    _serve(
        monkeypatch,
        lambda _r: httpx.Response(
            200, stream=Tripwire(), headers={"content-type": "application/pdf"}
        ),
    )
    result = await _adapter().fetch_page("https://a.example.com/x.pdf", resolver=_public)
    assert result.health.status is HealthStatus.UNREACHABLE
