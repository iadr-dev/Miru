"""The live half of event-driven alert evaluation: react to the ingest leader's `data.updated`
signal the moment a dataset lands, instead of waiting for the next reconciliation sweep.

The signal is a *latency optimisation only* (ADR-0028 §3). It is best-effort by design — a publish
failure never fails an ingest run, and several ingest jobs publish nothing — so correctness rests on
the sweep (`SchedulerWorker.reconcile_alerts`, run every daemon pass). Both paths converge on
`handle_data_updated` → `_evaluate_group`, which is idempotent per `(capability, market, as_of)`
through the eval watermark and the notification dedupe key, so a signal racing the sweep (or a
redelivered signal) can never double-notify.

Nothing here can take the daemon down: a malformed event is skipped, a failing evaluation is
logged, and a dropped subscription is retried with a pause rather than ending the task.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Protocol

import structlog

from events.bus import Event
from schemas.enums import Capability, Market

logger = structlog.get_logger(__name__)

_DATA_UPDATED_KIND = "data.updated"
_RESUBSCRIBE_DELAY_SECONDS = 5.0

UpdateSource = Callable[[], AsyncIterator[Event]]


class AlertSignalHandler(Protocol):
    async def handle_data_updated(
        self, *, capability: Capability, market: Market, as_of: str
    ) -> list[str]: ...


def parse_update(event: Event) -> tuple[Capability, Market, str] | None:
    """The `(capability, market, as_of)` a `data.updated` event carries, or None if it is some
    other event or malformed. The signal is untrusted plumbing: a bad field is a skip, never a
    crash."""
    if event.kind != _DATA_UPDATED_KIND:
        return None
    data = event.data
    try:
        capability = Capability(str(data["capability"]))
        market = Market(str(data["market"]))
        as_of = str(data["as_of"])
    except (KeyError, ValueError):
        return None
    return capability, market, as_of


async def consume_updates(handler: AlertSignalHandler, updates: AsyncIterator[Event]) -> None:
    """Drain one subscription, evaluating each well-formed signal. Returns when the stream ends;
    lets a transport error propagate so the caller can resubscribe."""
    async for event in updates:
        parsed = parse_update(event)
        if parsed is None:
            continue
        capability, market, as_of = parsed
        try:
            fired = await handler.handle_data_updated(
                capability=capability, market=market, as_of=as_of
            )
        except Exception:
            logger.exception("alert_signal_failed", capability=capability.value, as_of=as_of)
            continue
        if fired:
            logger.info("alert_signal_fired", capability=capability.value, fired=len(fired))


async def run_subscriber(
    handler: AlertSignalHandler,
    source: UpdateSource,
    *,
    stop: asyncio.Event,
    retry_delay: float = _RESUBSCRIBE_DELAY_SECONDS,
) -> None:
    """Keep a subscription alive until `stop` is set. A dropped or failing subscription is
    reopened after `retry_delay`; meanwhile the per-pass sweep keeps alerts correct."""
    while not stop.is_set():
        try:
            await consume_updates(handler, source())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("alert_subscription_dropped")
        if stop.is_set():
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=retry_delay)
    logger.info("alert_subscriber_exited")


__all__ = [
    "AlertSignalHandler",
    "UpdateSource",
    "consume_updates",
    "parse_update",
    "run_subscriber",
]
