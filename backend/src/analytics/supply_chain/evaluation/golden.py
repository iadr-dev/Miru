"""The golden set: hand-labelled positions the classifier is measured against.

`<reference dir>/supply_chain/golden_positions.yaml` is a YAML list of rows::

    - {symbol: "1234", chain_key: <industry name>, position: upstream,
       label_confidence: sure, note: "why"}

`label_confidence` is `sure` or `unsure`; only `sure` rows count towards accuracy. The labels
are the operator's private data (never in git); tests use invented ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analytics.supply_chain.positions.models import Position
from config import private_data
from config.private_data import YAML_SUFFIXES, UnsafeReferencePathError, safe_reference_file

GOLDEN_FILE = ("supply_chain", "golden_positions.yaml")


class LabelConfidence(StrEnum):
    SURE = "sure"
    UNSURE = "unsure"


class GoldenLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    chain_key: str = Field(min_length=1)
    position: Position
    label_confidence: LabelConfidence = LabelConfidence.SURE
    note: str = ""


class GoldenFileError(ValueError):
    """The golden file is missing, unreadable or has an invalid row."""


def parse_golden(raw: object, *, name: str = "golden_positions.yaml") -> list[GoldenLabel]:
    if not isinstance(raw, list):
        raise GoldenFileError(f"{name}: expected a list of rows")
    labels: list[GoldenLabel] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(cast("Sequence[object]", raw)):
        try:
            label = GoldenLabel.model_validate(row)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "row"
            raise GoldenFileError(f"{name} row[{i}]: {where}: {first['msg']}") from exc
        key = (label.symbol, label.chain_key)
        if key in seen:
            raise GoldenFileError(f"{name} row[{i}]: duplicate label for {label.symbol}")
        seen.add(key)
        labels.append(label)
    return labels


GOLDEN_PATH_MESSAGE = (
    "golden file must be an existing .yaml file inside the reference data directory"
)


def load_golden(path: Path | None = None) -> list[GoldenLabel]:
    candidate = path or private_data.reference_path(*GOLDEN_FILE)
    try:
        target = safe_reference_file(private_data.reference_dir(), candidate, YAML_SUFFIXES)
    except UnsafeReferencePathError as exc:
        raise GoldenFileError(f"{candidate.name}: {GOLDEN_PATH_MESSAGE}") from exc
    try:
        raw: object = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GoldenFileError(f"{target.name}: cannot read ({exc})") from exc
    return parse_golden(raw or [], name=target.name)


__all__ = [
    "GOLDEN_FILE",
    "GoldenFileError",
    "GoldenLabel",
    "LabelConfidence",
    "load_golden",
    "parse_golden",
]
