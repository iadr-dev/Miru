"""The SourceAdapter Protocol — the sole network-I/O contract.

Callers (the data-access router in `dataaccess/`, or `ingest/`) never construct an adapter
concretely — they depend on this Protocol.
"""

from __future__ import annotations

from typing import Final, Protocol

from schemas.enums import Capability, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import SourceResult


class SourceAdapter(Protocol):
    """One upstream source. Fetches, validates, and tags a result with provenance.

    `fetch` is per-`(capability, symbol)` — never a full-market scan;
    whole-market-by-date is the ingest layer's carve-out, not this seam.
    """

    source_id: str
    timing_class: TimingClass

    def supports(self) -> set[tuple[Capability, Market]]:
        """Declare which `(capability, market)` pairs this adapter can serve.

        The router (`dataaccess/router.py`) reads this to decide whether to try this
        adapter at all — adding a market/capability to an adapter is config, not a change
        to every caller.
        """
        ...

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        """Fetch one capability for one symbol. Async — never blocks the event loop.

        Blocking SDKs (e.g. yfinance) must be wrapped in `anyio.to_thread.run_sync`.
        """
        ...

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        """Validate a raw upstream payload against this source's contract.

        Raises `ValueError` (or a subclass) on contract violation — never silently accepts
        malformed data.
        """
        ...


# The `SourceHealth.detail` of a whole-market answer in which EVERY exchange said, explicitly, that
# it published nothing for the date (status `STALE`). The chain reads it to tell a market closure
# from a transport or parse failure; no other detail may equal it.
NO_DATA_PUBLISHED: Final = "no rows published (TWSE and TPEx explicitly report no data)"


class WholeMarketSource(Protocol):
    """An ingest-only whole-market-by-date source (`FinMindAdapter`, the exchange reports).

    Not part of `SourceAdapter`: never reachable through the per-symbol router. The payload is
    `{row_key: [rows]}`; a healthy but empty answer is `HealthStatus.STALE`, a failure anything
    else but `OK`."""

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult: ...
