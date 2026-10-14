"""A stopping API ends its idle feeds at once and keeps the chat turns for the grace window.

Before: on SIGTERM uvicorn waited for every open connection, and the idle feeds (`/updates/stream`,
`/quote/stream`) never finish, so each deploy spent the whole grace period waiting on them and was
then killed (exit 137) with the chat turns still unfinished. There was also no signal to a load
balancer that the replica was going away."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from api.lifecycle import ShutdownSignal


async def _endless(closed: list[bool]) -> AsyncIterator[str]:
    try:
        while True:
            await asyncio.sleep(3600)
            yield "x"
    finally:
        closed.append(True)


async def test_an_idle_feed_ends_when_shutdown_begins_and_is_closed() -> None:
    shutdown = ShutdownSignal()
    closed: list[bool] = []
    relay = shutdown.until_draining(_endless(closed))
    reader = asyncio.ensure_future(anext(relay))
    await asyncio.sleep(0.05)

    shutdown.begin()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(reader, timeout=2)
    assert closed == [True]


async def test_frames_flow_until_shutdown_begins() -> None:
    shutdown = ShutdownSignal()

    async def frames() -> AsyncIterator[str]:
        for n in range(3):
            yield f"f{n}"
            await asyncio.sleep(0)
        await asyncio.sleep(3600)

    relay = shutdown.until_draining(frames())

    assert [await anext(relay) for _ in range(3)] == ["f0", "f1", "f2"]
    shutdown.begin()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(relay), timeout=2)


async def test_a_feed_that_ends_by_itself_ends_the_relay() -> None:
    async def short() -> AsyncIterator[str]:
        yield "only"

    relay = ShutdownSignal().until_draining(short())

    assert await anext(relay) == "only"
    with pytest.raises(StopAsyncIteration):
        await anext(relay)


# ---- a real uvicorn process, a real SIGTERM ----------------------------------------------------

_SERVER = """
import asyncio, sys
from collections.abc import AsyncIterator
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from api.lifecycle import ShutdownSignal

shutdown = ShutdownSignal()
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_):
    shutdown.install()
    yield

app = FastAPI(lifespan=lifespan)

async def idle() -> AsyncIterator[str]:
    yield ": open\\n\\n"
    await asyncio.sleep(3600)
    yield "never"

async def turn() -> AsyncIterator[str]:
    yield "turn started\\n"
    await asyncio.sleep(2)  # a chat turn still answering when the signal arrives
    yield "turn finished\\n"

@app.get("/feed")
async def feed():
    return StreamingResponse(shutdown.until_draining(idle()), media_type="text/event-stream")

@app.get("/turn")
async def the_turn():
    return StreamingResponse(turn(), media_type="text/plain")

@app.get("/readyz")
async def readyz():
    from fastapi import Response
    return Response(status_code=503 if shutdown.draining else 200)
"""


def _start_server(tmp_path: Path, port: int) -> subprocess.Popen[bytes]:
    (tmp_path / "server.py").write_text(_SERVER)
    src = str(Path(__file__).resolve().parents[2] / "src")
    return subprocess.Popen(  # noqa: S603 - a test server on a loopback port
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--port",
            str(port),
            "--timeout-graceful-shutdown",
            "10",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{src}"},
    )


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX SIGTERM semantics (the container is Linux)"
)
async def test_a_sigterm_ends_the_idle_feed_at_once_and_lets_the_turn_finish(
    tmp_path: Path,
) -> None:
    port = 18765
    proc = _start_server(tmp_path, port)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(100):
                try:
                    if (await client.get(f"{base}/readyz")).status_code == 200:
                        break
                except httpx.TransportError:
                    await asyncio.sleep(0.1)
            feed_ended_after: list[float] = []
            turn_text: list[str] = []

            async def hold_feed() -> None:
                async with client.stream("GET", f"{base}/feed") as response:
                    async for _ in response.aiter_text():
                        pass
                feed_ended_after.append(time.monotonic() - sent_at)

            async def hold_turn() -> None:
                async with client.stream("GET", f"{base}/turn") as response:
                    turn_text.append("".join([c async for c in response.aiter_text()]))

            sent_at = time.monotonic()
            feed = asyncio.create_task(hold_feed())
            turn = asyncio.create_task(hold_turn())
            await asyncio.sleep(0.5)

            sent_at = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(asyncio.gather(feed, turn), timeout=15)

        assert feed_ended_after[0] < 1.5  # the idle feed did not hold the shutdown
        assert "turn finished" in turn_text[0]  # the turn got its time
        # and the process ended by itself (uvicorn re-raises the SIGTERM it caught), not killed
        assert proc.wait(timeout=10) in (0, -signal.SIGTERM)
    finally:
        if proc.poll() is None:
            proc.kill()
