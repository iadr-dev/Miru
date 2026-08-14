"""`raw_query` — the generic "fetch any TDCC OpenData dataset id" escape hatch backing
`tools.tdcc_tools.GetTdccRawDatasetTool` via `dataaccess.tdcc_query.TdccQueryGateway`.

Not part of `TdccAdapter`/`SourceAdapter` — a raw dataset id is a query shape, not a
`(capability, symbol)` pair (see `tools/tdcc_tools.py`'s docstring for the full rationale). This
module still does its own network I/O through the same `sources.tls.build_async_client`
chokepoint every other adapter in this package uses — it just isn't reached through
`DataAccessRouter.fetch()`.

Encoding is always attempted as UTF-8-with-BOM first, falling back to Big5 on decode failure —
this escape hatch covers *any* dataset id (including ones this package's own `_registry.py`
doesn't know about), so it can't consult a per-dataset `_DatasetConfig.encoding` the way
`TdccAdapter._call` does; the two encodings this upstream is confirmed to use (see
`_shared.py`'s module docstring) are attempted in order instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._shared import parse_csv

_BASE_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
_NO_DATA_MARKER = "No Data!"

_CAVEAT = (
    "TDCC OpenData raw dataset query — an escape hatch for datasets without a dedicated typed "
    "tool; no per-dataset currentness/cadence guarantee beyond 'as currently published by TDCC'."
)


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("big5")


async def raw_query(
    dataset_id: str,
    *,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
    symbol_column: str | None = None,
    symbol_value: str | None = None,
) -> SourceResult:
    # Resolved through the package namespace (not a module-level import) so tests can
    # monkeypatch the public `sources.tdcc.build_async_client` re-export, mirroring
    # `sources/finmind/_adapter.py`'s own deferred-import rationale.
    import sources.tdcc as _tdcc_pkg  # noqa: PLC0415

    try:
        async with _tdcc_pkg.build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(_BASE_URL, params={"id": dataset_id})
            response.raise_for_status()
            body = response.content
    except httpx.HTTPError as exc:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
            ),
        )

    try:
        text = _decode(body)
    except UnicodeDecodeError as exc:
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    if text.strip() == _NO_DATA_MARKER:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail=f"TDCC dataset {dataset_id} returned no data"
            ),
        )

    header, rows = parse_csv(text)
    if symbol_column is not None and symbol_value is not None:
        rows = [row for row in rows if row.get(symbol_column, "").strip() == symbol_value.strip()]

    status = HealthStatus.OK if rows else HealthStatus.STALE
    detail = "ok" if rows else "no matching rows"
    return _result(
        payload={"header": header, "rows": rows},
        health=SourceHealth(status=status, detail=detail),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id="TDCC",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["raw_query"]
