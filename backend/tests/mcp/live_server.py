"""Run the real MCP ASGI app under a real uvicorn server on an ephemeral port, in the test's own
event loop, so a real HTTP client (the official `mcp` client, `fastmcp.Client`, raw `httpx`)
drives exactly the bytes a remote Claude connector would send. Lifespan runs, so the streamable
HTTP session manager is live."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import uvicorn
from starlette.types import ASGIApp


@dataclass(frozen=True)
class LiveServer:
    host: str
    port: int

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp"


@contextlib.asynccontextmanager
async def serve(app: ASGIApp) -> AsyncIterator[LiveServer]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started and server.servers:
                break
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        else:  # pragma: no cover
            raise RuntimeError("uvicorn did not start")
        sockets = server.servers[0].sockets
        port = int(sockets[0].getsockname()[1])
        yield LiveServer(host="127.0.0.1", port=port)
    finally:
        server.should_exit = True
        await task
