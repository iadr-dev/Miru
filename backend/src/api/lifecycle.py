"""Process shutdown, as the request path sees it.

On SIGTERM uvicorn stops accepting connections and then waits for the open ones to finish, up to
`--timeout-graceful-shutdown`. Two kinds of connection are open at that moment, and they need
opposite treatment:

- a **chat turn** is worth waiting for: it is mid-answer, and the user would lose it;
- the **idle feeds** (`/updates/stream`, `/quote/stream`) never finish on their own, so waiting for
  them just burns the whole grace period on every deploy until the orchestrator kills the process
  (exit 137) with the turns still unfinished.

`ShutdownSignal` is set the moment the signal arrives (uvicorn's own lifespan shutdown runs only
after the wait, too late to matter): the idle feeds end at once, new readiness probes report
"draining" so a load balancer stops routing here, and the turns get the whole window."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
from collections.abc import AsyncIterator, Callable
from types import FrameType


class ShutdownSignal:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def draining(self) -> bool:
        return self._event.is_set()

    def begin(self) -> None:
        self._event.set()

    def install(self) -> None:
        """Set this when SIGTERM/SIGINT arrives, chained in front of the handler already there
        (uvicorn's, which starts its own drain). Only possible from the main thread; a no-op
        elsewhere (the test client runs the app on a worker thread)."""
        if threading.current_thread() is not threading.main_thread():
            return
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(signum)

            chained = previous if callable(previous) else None

            def handler(
                received: int,
                frame: FrameType | None,
                chained: Callable[[int, FrameType | None], object] | None = chained,
            ) -> None:
                loop.call_soon_threadsafe(self.begin)
                if chained is not None:
                    chained(received, frame)

            signal.signal(signum, handler)

    async def until_draining(self, frames: AsyncIterator[str]) -> AsyncIterator[str]:
        """Relay `frames` until shutdown begins, then end the stream (and close `frames`)."""
        next_frame: asyncio.Task[str] | None = None
        stopped = asyncio.ensure_future(self._event.wait())
        try:
            while True:
                if next_frame is None:
                    next_frame = asyncio.ensure_future(anext(frames))
                done, _ = await asyncio.wait(
                    {next_frame, stopped}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_frame not in done:
                    return  # draining: the pending read is cancelled below
                finished, next_frame = next_frame, None
                try:
                    frame = finished.result()
                except StopAsyncIteration:
                    return
                yield frame
        finally:
            for task in (stopped, next_frame):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                        await task
            aclose = getattr(frames, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()


__all__ = ["ShutdownSignal"]
