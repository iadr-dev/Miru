"""The standalone MCP process — `python -m mcp`.

A separate remote streamable-HTTP process, never wired into the main API process — matching how
`ingest/__main__.py` and `scheduler/__main__.py` are already separate `__main__.py` processes in
this architecture rather than background tasks bolted onto the API worker. Unlike those two
(which loop on an interval firing due jobs), this process is a plain long-running ASGI server:
`mcp.composition.build_mcp_http_app` assembles the real tool registry via the same
`orchestrator.composition` composition-root functions the API app uses, and this module hands the
resulting Starlette app to `uvicorn` to serve — `build_http_app` returns an ASGI app for a runner
to serve, it has no `.run()` of its own (see `mcp/app.py`'s module docstring).

Graceful shutdown is uvicorn's own SIGTERM/SIGINT handling — every MCP tool call here is one
bounded read (`dispatch_tool` on an already-fetched or per-call source read), never a
multi-step write, so there is no in-flight-batch/atomic-swap concern to coordinate on shutdown
the way the ingest/scheduler daemons do.
"""

from __future__ import annotations

import asyncio

import uvicorn

from config.settings import get_settings
from db.base import ensure_schema_for
from mcp.composition import build_mcp_http_app


def main() -> None:
    settings = get_settings()
    built = build_mcp_http_app(settings)
    # `create_all()` is idempotent, so this is safe even under multiple replicas racing to call
    # it. Run once, synchronously, before `uvicorn.run` starts its own loop — this project's sole
    # schema-provisioning mechanism (no Alembic migration chain).
    asyncio.run(ensure_schema_for(built.session_factory))
    uvicorn.run(built.app, host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
