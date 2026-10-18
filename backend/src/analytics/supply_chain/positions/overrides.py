"""The operator's optional position overrides.

`<reference dir>/supply_chain/position_overrides.yaml` is a YAML list of rows::

    - {symbol: "1234", market: TW, chain_key: <industry name>, position: upstream, reason: "..."}

`position` is upstream | midstream | downstream and `reason` is required. A missing file means no
overrides. Any bad row fails the whole load with a message naming the file and the row.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from analytics.supply_chain.positions.models import Override
from config.private_data import reference_path

OVERRIDES_FILE = ("supply_chain", "position_overrides.yaml")


class OverrideFileError(ValueError):
    """The overrides file is unreadable or has an invalid row."""


def parse_overrides(raw: object, *, name: str = "position_overrides.yaml") -> list[Override]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OverrideFileError(f"{name}: expected a list of rows")
    out: list[Override] = []
    seen: set[tuple[str, str, str]] = set()
    for i, row in enumerate(cast("Sequence[object]", raw)):
        try:
            override = Override.model_validate(row)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "row"
            raise OverrideFileError(f"{name} row[{i}]: {where}: {first['msg']}") from exc
        key = (override.symbol, override.market, override.chain_key)
        if key in seen:
            raise OverrideFileError(
                f"{name} row[{i}]: duplicate override for {override.symbol} in "
                f"{override.chain_key!r}"
            )
        seen.add(key)
        out.append(override)
    return out


def load_overrides(path: Path | None = None) -> list[Override]:
    target = path or reference_path(*OVERRIDES_FILE)
    if not target.is_file():
        return []
    try:
        raw: object = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OverrideFileError(f"{target.name}: cannot read ({exc})") from exc
    return parse_overrides(raw, name=target.name)


__all__ = ["OVERRIDES_FILE", "OverrideFileError", "load_overrides", "parse_overrides"]
