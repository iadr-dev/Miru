"""TDCC (集保結算所) OpenData adapter — free, public, keyless CSV dumps.

Every dataset this adapter serves is a **whole-market CSV dump** at
`https://opendata.tdcc.com.tw/getOD.ashx?id=<dataset_id>` — there is no per-symbol query
parameter upstream (verified live: passing e.g. `&STK_NO=2330` or `&D=<date>` has no effect on
the response). `fetch()` therefore always makes exactly one upstream GET and filters the parsed
rows to the requested symbol client-side, the same shape `sources/mops.py` already established
for this "whole-market-dump-filtered-per-call" family — never a whole-market path through this
adapter's own `fetch()` (that stays the ingest layer's carve-out elsewhere in this codebase;
this adapter has no `fetch_whole_market` at all, since every dataset here is genuinely small
enough, and genuinely lacking a documented whole-market form of its own worth building a
separate ingest path for, that per-symbol-filtered-client-side is the only mode this pass needs).

Real upstream quirks confirmed by hand (`curl` against the live endpoint, not assumed):
- Every dataset responds `Content-Type: text/csv; charset=UTF-8`, but datasets `6-1`/`6-2`
  (e-voting) are genuinely Big5-encoded bytes regardless of that header — decoding them as
  UTF-8 raises `UnicodeDecodeError`. `_DatasetConfig.encoding` names the real encoding per
  dataset; this adapter decodes with it, never blindly trusting the response header.
- Several symbol/fund-code columns are fixed-width, space-padded on the right (e.g. `"2330  "`
  for a 4-character stock id) — every row mapper strips these before use.
- `https://opendata.tdcc.com.tw/` itself 302-redirects plain browser navigation to a security
  landing page; the CSV endpoint (`getOD.ashx?id=...`) is unaffected — verified.
"""

from __future__ import annotations

from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._registry import _DATASETS
from ._shared import _DatasetConfig, _result, parse_csv

logger = structlog.get_logger(__name__)

_BASE_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
_NO_DATA_MARKER = "No Data!"  # this upstream's own literal body for an empty/unknown dataset id


class TdccAdapter:
    """The `TDCC` source adapter."""

    source_id = "TDCC"
    timing_class = TimingClass.OFFICIAL_EOD  # per-dataset override happens in fetch()

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(cap, config.market) for cap, config in _DATASETS.items()}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        """Validate the parsed-CSV shape. Raises `ValueError` on contract violation."""
        if "rows" not in raw:
            raise ValueError("TDCC response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TDCC 'rows' field is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TDCC CSV row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        config = _DATASETS.get(cap)
        if config is None or sym.market is not config.market:
            return self._unavailable(cap, sym, config)
        return await self._call(config, sym=sym, tls_policy=tls_policy)

    def _unavailable(
        self, cap: Capability, sym: Symbol, config: _DatasetConfig | None
    ) -> SourceResult:
        if config is None:
            return _result(
                data_class=DataClass.PUBLIC,
                timing_class=TimingClass.REFERENCE,
                caveat=f"TDCC does not serve capability {cap.value} for market {sym.market.value}.",
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail="capability not declared by this adapter for this market",
                ),
            )
        return _result(
            data_class=config.data_class,
            timing_class=config.timing_class,
            caveat=config.caveat,
            payload={},
            health=SourceHealth(
                status=HealthStatus.UNAVAILABLE_TIER,
                detail=f"TDCC {config.dataset_id} does not cover market {sym.market.value}",
            ),
        )

    async def _fetch_raw(
        self, config: _DatasetConfig, *, tls_policy: TLSPolicy
    ) -> str | SourceResult:
        """The HTTP round trip + its error mapping, pulled out of `_call` purely to keep that
        method's branching shallow. Returns the decoded CSV text on success, or an
        already-built error `SourceResult` the caller should return as-is."""
        # Resolved through the package namespace (not a module-level import) so tests can
        # monkeypatch the public `sources.tdcc.build_async_client` re-export, mirroring
        # `sources/finmind/_adapter.py`'s own deferred-import rationale.
        import sources.tdcc as _tdcc_pkg  # noqa: PLC0415

        try:
            async with _tdcc_pkg.build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_BASE_URL, params={"id": config.dataset_id})
                response.raise_for_status()
                body = response.content
        except httpx.HTTPError as exc:
            return self._error_result(config, detail=f"transport error: {type(exc).__name__}")

        try:
            text = body.decode(config.encoding)
        except UnicodeDecodeError as exc:
            return self._error_result(config, detail=f"decode error: {exc}")
        if text.strip() == _NO_DATA_MARKER:
            return self._error_result(
                config, detail=f"TDCC dataset {config.dataset_id} returned no data"
            )
        return text

    def _error_result(self, config: _DatasetConfig, *, detail: str) -> SourceResult:
        return _result(
            data_class=config.data_class,
            timing_class=config.timing_class,
            caveat=config.caveat,
            payload={},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=detail),
        )

    async def _call(
        self, config: _DatasetConfig, *, sym: Symbol, tls_policy: TLSPolicy
    ) -> SourceResult:
        text = await self._fetch_raw(config, tls_policy=tls_policy)
        if isinstance(text, SourceResult):
            return text

        _header, all_rows = parse_csv(text)
        matched_rows = self._filter_to_symbol(all_rows, config, sym)

        try:
            validated = self.validate({"rows": matched_rows})
        except ValueError as exc:
            return _result(
                data_class=config.data_class,
                timing_class=config.timing_class,
                caveat=config.caveat,
                payload={},
                health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc)),
            )

        raw_rows = validated["rows"]
        if not isinstance(raw_rows, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'rows' is not a list")
        typed_rows = cast("list[dict[str, str]]", raw_rows)
        normalized = config.row_mapper(typed_rows, sym if config.symbol_column else None)
        if not normalized:
            return _result(
                data_class=config.data_class,
                timing_class=config.timing_class,
                caveat=config.caveat,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"symbol {sym.id} not found in TDCC dataset {config.dataset_id}'s "
                    "current dump",
                ),
            )
        return _result(
            data_class=config.data_class,
            timing_class=config.timing_class,
            caveat=config.caveat,
            payload={config.row_key: normalized},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )

    @staticmethod
    def _filter_to_symbol(
        rows: list[dict[str, str]], config: _DatasetConfig, sym: Symbol
    ) -> list[dict[str, str]]:
        """Market-level datasets (`symbol_column is None`) return every row unfiltered; every
        other dataset filters to rows whose `symbol_column` cell — stripped of the fixed-width
        padding this upstream uses — matches the requested symbol."""
        if config.symbol_column is None:
            return rows
        return [row for row in rows if row.get(config.symbol_column, "").strip() == sym.id.strip()]


__all__ = ["TdccAdapter"]
