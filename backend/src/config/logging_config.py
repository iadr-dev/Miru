"""Process-wide structured logging.

Every process (api, scheduler, ingest, mcp) calls `configure_logging` once at startup. Output is
one JSON object per line in production (so a log platform can index `request_id`, `user_id`,
`route`, `status`) and readable console lines in development. Standard-library loggers (uvicorn,
SQLAlchemy, third-party libraries) are routed through the same pipeline, so nothing is left as
unparsed text. Request-scoped fields (`request_id`, `user_id`) travel in structlog's contextvars
and are merged into every line logged while handling that request."""

from __future__ import annotations

import logging

import structlog
from structlog.typing import Processor

from config.settings import Settings


def json_logging(settings: Settings) -> bool:
    return settings.log_format == "json" or (
        settings.log_format == "auto" and settings.env == "production"
    )


def configure_logging(settings: Settings) -> None:
    """Idempotent: safe to call again (for example from a test or a reloaded process)."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Not cached, so `structlog.testing.capture_logs()` can still intercept in tests.
        cache_logger_on_first_use=False,
    )
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logging(settings)
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers; hand its records to the root pipeline instead. Its access
    # log is replaced by the request middleware's single structured line.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False


__all__ = ["configure_logging", "json_logging"]
