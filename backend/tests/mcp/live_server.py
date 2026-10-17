"""Run the real MCP ASGI app under a real uvicorn server on an ephemeral port, so a real HTTP
client (the official `mcp` client, `fastmcp.Client`, raw `httpx`) drives exactly the bytes a
remote Claude connector would send. Lifespan runs, so the streamable HTTP session manager is
live.

The server runs on its own thread with its own event loop, like a real deployment where the
server and its client are different processes. Sharing the test's loop worked for the first two
or three servers in a process and then every later one answered `200` with a body cut short
(`ASGI callable returned without completing response`): the SDK's per-request stream handling
and the client's task groups interfere when both live on one loop. Anything a test shares with the
server (events, counters) must therefore be thread-safe."""

from __future__ import annotations

import asyncio
import contextlib
import threading
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
async def serve(app: ASGIApp, *, port: int = 0) -> AsyncIterator[LiveServer]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="live-mcp-server", daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started and server.servers:
                break
            if not thread.is_alive():
                raise RuntimeError("uvicorn exited before it started")
            await asyncio.sleep(0.01)
        else:  # pragma: no cover
            raise RuntimeError("uvicorn did not start")
        sockets = server.servers[0].sockets
        port = int(sockets[0].getsockname()[1])
        yield LiveServer(host="127.0.0.1", port=port)
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 15)
