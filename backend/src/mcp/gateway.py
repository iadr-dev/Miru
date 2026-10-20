"""The HTTP edge of the MCP endpoint - one pure-ASGI middleware in front of FastMCP's app.

It exists because Anthropic's connectors (claude.ai / Claude Desktop custom connectors, the
Messages API `mcp_servers` connector, Claude Code `--transport http`) all reach this server from
the public internet, through a tunnel or reverse proxy, and need behavior the MCP SDK does not
give out of the box:

- `/mcp/` and `/mcp` are the same endpoint. Starlette would answer `/mcp/` with a 307 whose
  `Location` is built from the *inner* scheme (`http://`), which a client behind a TLS-terminating
  proxy then follows to a URL that does not exist. The path is normalized in place instead.
- Sign-in (MCP authorization spec). With `require_auth` a request without a valid credential is
  answered HTTP 401 with a `WWW-Authenticate` challenge of the Bearer scheme that carries the
  `resource_metadata` URL and the `mcp:read` scope parameter (RFC 9728 section 5.1) - the signal
  every MCP client waits for before it starts its OAuth flow; an invalid or expired token
  adds `error="invalid_token"`. Without `require_auth` (local development) a request with no
  credential stays anonymous, while a presented-but-bad bearer is still a 401.
- Abuse bounds: a per-principal request rate (user id once a token verifies, else client
  address), a request-body cap and a concurrent-request cap.
- A rejected `Host` / `Origin` (FastMCP answers a bare "Misdirected Request") gets a body that
  says which setting to change.

Only `scope["path"] == mcp_path` is policed; `/healthz`, the `/.well-known/*` documents and CORS
preflight (`OPTIONS`) pass straight through. No secret or token is ever logged.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from events.rate_limit import AsyncRateLimiter
from mcp.auth import TokenVerdict

logger = structlog.get_logger(__name__)

_WINDOW_SECONDS = 60
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_PAYLOAD_TOO_LARGE = 413
_HTTP_MISDIRECTED = 421
_HTTP_TOO_MANY = 429
_HTTP_UNAVAILABLE = 503

# Which user a credential authenticates (or why it does not).
TokenVerifier = Callable[[str], Awaitable[TokenVerdict]]


@dataclass(frozen=True)
class GatewayConfig:
    mcp_path: str
    max_body_bytes: int
    max_concurrent_requests: int
    rate_anonymous_per_minute: int
    rate_signed_in_per_minute: int
    trusted_proxy_count: int = 0
    # Absolute URL of the RFC 9728 metadata document, advertised in `WWW-Authenticate`. None when
    # no authorization server is configured: the challenge then carries no pointer.
    resource_metadata_url: str | None = None
    # true = no credential, no service. Fail closed: a config built without saying otherwise
    # demands sign-in.
    require_auth: bool = True
    scope: str = "mcp:read"


class _BodyTooLargeError(Exception):
    pass


def _client_address(scope: Scope, headers: Headers, *, trusted_proxy_count: int) -> str:
    """The caller's address. Behind `trusted_proxy_count` proxies the caller is the
    `X-Forwarded-For` entry that many hops from the RIGHT (everything to its left is client
    supplied). With no trusted proxy the header is ignored."""
    if trusted_proxy_count > 0:
        hops = [h.strip() for h in ",".join(headers.getlist("x-forwarded-for")).split(",")]
        hops = [h for h in hops if h]
        if len(hops) >= trusted_proxy_count:
            try:
                return str(ipaddress.ip_address(hops[-trusted_proxy_count]))
            except ValueError:
                pass  # not an address: fall back to the socket peer rather than key on junk
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


async def _send_json(
    send: Send, status: int, payload: dict[str, object], extra: list[tuple[bytes, bytes]]
) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class McpGateway:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: GatewayConfig,
        rate_limiter: AsyncRateLimiter,
        verify_token: TokenVerifier,
    ) -> None:
        self.app = app
        self._config = config
        self._limiter = rate_limiter
        self._verify = verify_token
        self._in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cfg = self._config
        if scope["path"] == cfg.mcp_path + "/":
            scope = {**scope, "path": cfg.mcp_path, "raw_path": cfg.mcp_path.encode()}
        if scope["path"] != cfg.mcp_path:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if scope["method"] == "OPTIONS":  # CORS preflight carries no credentials by design
            await self._serve(scope, headers, receive, send)
            return
        if not await self._admit(scope, headers, send):
            return
        await self._serve(scope, headers, receive, send)

    async def _admit(self, scope: Scope, headers: Headers, send: Send) -> bool:
        """Auth + rate limit. False means a 401/429 response was already sent."""
        cfg = self._config
        principal: str
        limit: int
        authorization = headers.get("authorization")
        api_key = headers.get("x-api-key")
        if authorization is not None:
            token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
            verdict = await self._verify(token) if token else TokenVerdict(None, "invalid")
            if verdict.user_id is None:
                await self._challenge(send, failure=verdict.failure or "invalid")
                return False
            principal, limit = f"user:{verdict.user_id}", cfg.rate_signed_in_per_minute
        elif api_key is not None and cfg.require_auth:
            verdict = await self._verify(api_key.strip())
            if verdict.user_id is None:
                await self._challenge(send, failure=verdict.failure or "invalid")
                return False
            principal, limit = f"user:{verdict.user_id}", cfg.rate_signed_in_per_minute
        elif cfg.require_auth:
            await self._challenge(send, failure=None)
            return False
        else:
            address = _client_address(scope, headers, trusted_proxy_count=cfg.trusted_proxy_count)
            principal, limit = f"ip:{address}", cfg.rate_anonymous_per_minute

        rate = await self._limiter.check(
            f"mcp:{principal}", max_requests=limit, window_seconds=_WINDOW_SECONDS
        )
        if not rate.allowed:
            retry = max(1, int(rate.retry_after_seconds or _WINDOW_SECONDS))
            logger.warning(
                "mcp_rate_limited",
                caller_kind=principal.split(":", 1)[0],
                limit_per_minute=limit,
            )
            await _send_json(
                send,
                _HTTP_TOO_MANY,
                {
                    "error": "rate_limited",
                    "message": f"Too many requests; retry in {retry} seconds.",
                },
                [(b"retry-after", str(retry).encode())],
            )
            return False
        return True

    async def _challenge(self, send: Send, *, failure: str | None) -> None:
        """401 with the RFC 6750 / RFC 9728 challenge. `failure=None` = no credential was sent."""
        cfg = self._config
        parts = ['Bearer realm="miru-mcp"']
        if cfg.resource_metadata_url:
            parts.append(f'resource_metadata="{cfg.resource_metadata_url}"')
        parts.append(f'scope="{cfg.scope}"')
        if failure is None:
            error, description = "unauthorized", "Sign in to use this server."
        else:
            error = "invalid_token"
            description = (
                "The access token has expired; sign in again."
                if failure == "expired"
                else "The access token is invalid; sign in again."
            )
            parts.append('error="invalid_token"')
            parts.append(f'error_description="{description}"')
        logger.info("mcp_unauthorized", credential_given=failure is not None, failure=failure)
        await _send_json(
            send,
            _HTTP_UNAUTHORIZED,
            {"error": error, "message": description},
            [(b"www-authenticate", ", ".join(parts).encode())],
        )

    async def _serve(self, scope: Scope, headers: Headers, receive: Receive, send: Send) -> None:
        cfg = self._config
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > cfg.max_body_bytes:
            await self._too_large(send)
            return

        counts_toward_cap = scope["method"] == "POST"  # a GET is a long-lived stream, not work
        if counts_toward_cap:
            if self._in_flight >= cfg.max_concurrent_requests:
                logger.warning("mcp_concurrency_cap_hit", cap=cfg.max_concurrent_requests)
                await _send_json(
                    send,
                    _HTTP_UNAVAILABLE,
                    {"error": "busy", "message": "Server is busy; retry shortly."},
                    [(b"retry-after", b"1")],
                )
                return
            self._in_flight += 1

        seen = 0
        started = False
        swallow = False
        host = headers.get("host", "")

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cfg.max_body_bytes:
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started, swallow
            if message["type"] == "http.response.start":
                status = message["status"]
                if status in (_HTTP_MISDIRECTED, _HTTP_FORBIDDEN):
                    swallow = True
                    await self._explain_rejection(send, status, host)
                    return
                started = True
            elif swallow:
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _BodyTooLargeError:
            if not started and not swallow:
                await self._too_large(send)
        finally:
            if counts_toward_cap:
                self._in_flight -= 1

    async def _too_large(self, send: Send) -> None:
        await _send_json(
            send,
            _HTTP_PAYLOAD_TOO_LARGE,
            {
                "error": "request_too_large",
                "message": f"Request body exceeds {self._config.max_body_bytes} bytes.",
            },
            [],
        )

    async def _explain_rejection(self, send: Send, status: int, host: str) -> None:
        if status == _HTTP_MISDIRECTED:
            logger.warning("mcp_host_rejected", host=host)
            payload: dict[str, object] = {
                "error": "host_not_allowed",
                "message": (
                    f"Host '{host}' is not allowed. Set MCP_PUBLIC_BASE_URL to this server's "
                    "public https URL (or add the hostname to MCP_ALLOWED_HOSTS) and restart."
                ),
            }
        else:
            logger.warning("mcp_origin_rejected")
            payload = {
                "error": "origin_not_allowed",
                "message": (
                    "The Origin header is not allowed. Add it to MCP_ALLOWED_ORIGINS "
                    "(browser-based clients only; server-side connectors send no Origin)."
                ),
            }
        await _send_json(send, status, payload, [])


__all__ = ["GatewayConfig", "McpGateway", "TokenVerifier"]
