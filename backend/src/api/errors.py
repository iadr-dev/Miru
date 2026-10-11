"""The standardized error envelope + exception handlers for the HTTP surface.

Every error the API returns is the same JSON shape `{code, message, retryable}` (the contract in
`api-reference.md`), never a bare FastAPI/Starlette default body — so a client parses one shape
regardless of which router raised. `ApiError` is the one exception a router raises to produce
that envelope with an explicit status; a `RequestValidationError` (bad input) and any
otherwise-unhandled exception are funneled through the same renderer so nothing leaks a stack
trace or an ad-hoc body.

Note what is deliberately NOT an error here: an unentitled caller hitting a `sponsor` feature is
answered with a masked-teaser payload at HTTP 200 (owned by `tools.base.dispatch_tool`), never a
401/403 — that is a product rule, not an error condition.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

# The numeric literal, not `status.HTTP_422_*` — the constant's name churns across Starlette
# versions (entity → content) and emits a deprecation warning; the code is stable.
_HTTP_422_UNPROCESSABLE = 422


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ApiError(Exception):
    """A router-raised error carrying the HTTP status + the envelope fields. `retryable` tells a
    client whether re-issuing the same request could succeed (a transient upstream/quota case)
    versus a permanent client error (bad auth, unknown id)."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


# Codes for the errors the framework raises itself (unknown route, wrong method, ...), so they
# share the envelope instead of Starlette's bare `{"detail": ...}`.
_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}
_RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})


def _render(
    status_code: int, body: ErrorBody, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def _validation_message(exc: RequestValidationError) -> str:
    """The first problem with where it is (`query.symbol`), so a client can tell which field."""
    errors = exc.errors()
    if not errors:
        return "Request failed validation."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"Request failed validation: {prefix}{first['msg']}"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(  # pyright: ignore[reportUnusedFunction] — registered via decorator
        _request: Request, exc: ApiError
    ) -> JSONResponse:
        return _render(
            exc.status_code,
            ErrorBody(code=exc.code, message=exc.message, retryable=exc.retryable),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(  # pyright: ignore[reportUnusedFunction] — registered via decorator
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's structured errors are collapsed to one human message; the client only needs
        # the envelope shape, and echoing raw validation internals is noise. A bad request is
        # never retryable as-is.
        return _render(
            _HTTP_422_UNPROCESSABLE,
            ErrorBody(code="invalid_request", message=_validation_message(exc), retryable=False),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(  # pyright: ignore[reportUnusedFunction] — registered via decorator
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code)
        if code is None:
            code = "internal_error" if exc.status_code >= 500 else "http_error"  # noqa: PLR2004
        message = exc.detail or code
        return _render(
            exc.status_code,
            ErrorBody(code=code, message=message, retryable=exc.status_code in _RETRYABLE_STATUSES),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(  # pyright: ignore[reportUnusedFunction] — registered via decorator
        _request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error("api_unhandled_exception", error=str(exc), error_type=type(exc).__name__)
        return _render(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorBody(
                code="internal_error",
                message="An unexpected error occurred.",
                retryable=True,
            ),
        )


__all__ = ["ApiError", "ErrorBody", "register_exception_handlers"]
