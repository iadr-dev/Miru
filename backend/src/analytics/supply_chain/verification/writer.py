"""The only writer of the private relation files (`<reference>/supply_chain/relations/*.yaml`).

Leader-only. Upserts by natural key (supplier, customer, kind, product), merges evidence by URL,
never deletes a row and never touches a `manual` or `seeded` row. Every touched file is staged
beside the original, validated with the loader's own parser, and only then swapped in with
`os.replace`; if anything fails, files already swapped are restored and the originals are intact.
A dry run plans the same changes and writes nothing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from analytics.supply_chain.domain.models import (
    Evidence,
    Kind,
    Origin,
    Status,
    Trust,
)
from analytics.supply_chain.refdata_io.loader import build_relation
from analytics.supply_chain.refdata_io.schema import RelationFileRow
from analytics.supply_chain.verification.models import ClaimResult, Company

logger = structlog.get_logger(__name__)

type Row = dict[str, object]
type RowKey = tuple[str, str, str, str, str, str]

LOCK_NAME = ".writer.lock"
_STALE_LOCK_SECONDS = 600
_PROTECTED = (Origin.MANUAL.value, Origin.SEEDED.value)


class WriterError(RuntimeError):
    """A write that could not be completed; nothing it touched is left half-written."""


class NotLeaderError(WriterError):
    """Only the ingest leader may write the relation files."""


@dataclass
class WriteReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    protected: int = 0
    files: list[str] = field(default_factory=lambda: [])
    dry_run: bool = False

    @property
    def rows_written(self) -> int:
        return self.created + self.updated


def file_name_for(supplier: Company) -> str:
    return f"{supplier.market}-{supplier.symbol}.yaml"


def _str(value: object) -> str:
    return "" if value is None else str(value)


def _endpoint(row: Row, side: str) -> tuple[str, str]:
    raw = row.get(side)
    node = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    return _str(node.get("market")), _str(node.get("symbol"))


def row_key(row: Row) -> RowKey:
    s_market, s_symbol = _endpoint(row, "supplier")
    c_market, c_symbol = _endpoint(row, "customer")
    return (
        s_market,
        s_symbol,
        c_market,
        c_symbol,
        _str(row.get("kind") or Kind.SUPPLIES.value),
        _str(row.get("product")),
    )


def result_key(result: ClaimResult) -> RowKey:
    return (
        result.supplier.market,
        result.supplier.symbol,
        result.customer.market,
        result.customer.symbol,
        result.kind.value,
        result.product or "",
    )


def _endpoint_node(company: Company) -> Row:
    return {"symbol": company.symbol, "market": company.market, "name": company.name}


def _evidence_row(item: Evidence) -> Row:
    return {
        "url": item.url,
        "publisher": item.publisher,
        "published_on": item.published_on,
        "excerpt": item.excerpt,
        "checker": item.checker.value,
    }


def _new_row(result: ClaimResult, today: date) -> Row:
    return {
        "supplier": _endpoint_node(result.supplier),
        "customer": _endpoint_node(result.customer),
        "kind": result.kind.value,
        "product": result.product,
        "status": result.status.value,
        "origin": Origin.RESEARCH.value,
        "revenue_pct": None,
        "as_of": result.as_of,
        "last_verified_at": today,
        "evidence": [_evidence_row(e) for e in result.evidence],
    }


def _merge_into(row: Row, result: ClaimResult, today: date) -> Row:
    """A copy of `row` with the result applied: evidence merged by URL, dates moved forward, and a
    verified result promoting the row. A candidate result never demotes a published row."""
    merged = dict(row)
    existing = cast("list[Row]", list(cast("list[object]", row.get("evidence") or [])))
    known = {_str(e.get("url")) for e in existing}
    merged["evidence"] = [
        *existing,
        *(_evidence_row(e) for e in result.evidence if e.url not in known),
    ]
    current = _str(row.get("status"))
    if result.status is Status.VERIFIED:
        merged["status"] = Status.VERIFIED.value
    elif current not in (Status.VERIFIED.value, Status.STALE.value):
        merged["status"] = result.status.value
    previous = row.get("as_of")
    if result.as_of is not None and (not isinstance(previous, date) or result.as_of > previous):
        merged["as_of"] = result.as_of
    merged["last_verified_at"] = today
    for side, company in (("supplier", result.supplier), ("customer", result.customer)):
        node = dict(cast("dict[str, object]", merged.get(side) or {}))
        if not node.get("name") and company.name:
            node["name"] = company.name
        merged[side] = node
    return merged


def validate_text(text: str, trust_table: Mapping[str, Trust], today: date) -> int:
    """Parse a staged file exactly as the loader would; returns the row count."""
    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WriterError(f"staged file is not valid YAML: {exc}") from exc
    if raw is None:
        return 0
    if not isinstance(raw, list):
        raise WriterError("staged file is not a list of rows")
    rows = cast("list[object]", raw)
    for index, item in enumerate(rows):
        try:
            build_relation(RelationFileRow.model_validate(item), trust_table, today)
        except ValidationError as exc:
            raise WriterError(
                f"staged row[{index}] is invalid: {exc.error_count()} problem(s)"
            ) from exc
    return len(rows)


def _dump(rows: Sequence[Row]) -> str:
    return yaml.safe_dump(list(rows), allow_unicode=True, sort_keys=False, width=100)


@contextmanager
def _locked(directory: Path, timeout: float) -> Generator[None]:
    lock = directory / LOCK_NAME
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _STALE_LOCK_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise WriterError("the relation files are locked by another writer") from None
            time.sleep(0.05)
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        break
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


class RelationWriter:
    def __init__(  # noqa: PLR0913 - keyword-only collaborators
        self,
        directory: Path,
        *,
        is_leader: Callable[[], bool],
        today: date,
        trust_table: Mapping[str, Trust] | None = None,
        dry_run: bool = False,
        lock_timeout: float = 10.0,
    ) -> None:
        self._dir = directory
        self._is_leader = is_leader
        self._today = today
        self._trust = dict(trust_table or {})
        self._dry = dry_run
        self._timeout = lock_timeout

    def apply(self, results: Sequence[ClaimResult]) -> WriteReport:
        if not self._is_leader():
            raise NotLeaderError("not the ingest leader - relation files are not written")
        report = WriteReport(dry_run=self._dry)
        if not results:
            return report
        if self._dry:
            self._plan(results, self._load_all(), report)
            return report
        self._dir.mkdir(parents=True, exist_ok=True)
        with _locked(self._dir, self._timeout):
            files = self._load_all()
            dirty = self._plan(results, files, report)
            self._commit(dirty)
            report.files = sorted(p.name for p in dirty)
        return report

    def _load_all(self) -> dict[Path, list[Row]]:
        files: dict[Path, list[Row]] = {}
        for path in sorted(self._dir.glob("*.yaml")) if self._dir.is_dir() else []:
            try:
                raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise WriterError(f"{path.name}: cannot read ({type(exc).__name__})") from exc
            if raw is None:
                files[path] = []
            elif isinstance(raw, list) and all(
                isinstance(r, dict) for r in cast("list[object]", raw)
            ):
                files[path] = cast("list[Row]", raw)
            else:
                raise WriterError(f"{path.name}: not a list of rows")
        return files

    def _plan(
        self, results: Sequence[ClaimResult], files: dict[Path, list[Row]], report: WriteReport
    ) -> dict[Path, list[Row]]:
        located: dict[RowKey, tuple[Path, int]] = {}
        for path, rows in files.items():
            for index, row in enumerate(rows):
                located.setdefault(row_key(row), (path, index))
        dirty: dict[Path, list[Row]] = {}
        for result in results:
            key = result_key(result)
            if key in located:
                path, index = located[key]
                rows = dirty.get(path) or [dict(r) for r in files[path]]
                current = rows[index]
                if _str(current.get("origin")) in _PROTECTED:
                    report.protected += 1
                    continue
                updated = _merge_into(current, result, self._today)
                if updated == current:
                    report.unchanged += 1
                    continue
                rows[index] = updated
                dirty[path] = rows
                report.updated += 1
                continue
            path = self._dir / file_name_for(result.supplier)
            rows = dirty.get(path) or [dict(r) for r in files.get(path, [])]
            rows.append(_new_row(result, self._today))
            dirty[path] = rows
            located[key] = (path, len(rows) - 1)
            report.created += 1
        return dirty

    def _commit(self, dirty: dict[Path, list[Row]]) -> None:
        staged: list[tuple[Path, Path]] = []
        swapped: list[tuple[Path, bytes | None]] = []
        try:
            for path, rows in dirty.items():
                text = _dump(rows)
                validate_text(text, self._trust, self._today)
                temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append((temp, path))
            for temp, path in staged:
                original = path.read_bytes() if path.is_file() else None
                temp.replace(path)
                swapped.append((path, original))
        except Exception as exc:
            self._rollback(staged, swapped)
            if isinstance(exc, WriterError):
                raise
            raise WriterError(
                f"relation write failed and was rolled back ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _rollback(
        staged: list[tuple[Path, Path]], swapped: list[tuple[Path, bytes | None]]
    ) -> None:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
        for path, original in reversed(swapped):
            if original is None:
                path.unlink(missing_ok=True)
            else:
                restore = path.with_name(f".{path.name}.restore-{os.getpid()}")
                restore.write_bytes(original)
                restore.replace(path)
        logger.warning("sc_research_write_rolled_back", restored=len(swapped))


__all__ = [
    "LOCK_NAME",
    "NotLeaderError",
    "RelationWriter",
    "WriteReport",
    "WriterError",
    "file_name_for",
    "result_key",
    "row_key",
    "validate_text",
]
