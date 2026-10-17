"""Where the private reference data lives and how to read it.

Supply-chain relations, concept themes and tier maps are the product's own research. They are
kept out of git: the operator holds them in a local directory (`REFERENCE_DATA_DIR`, mounted
read-only into the containers that load them). A fresh clone has none, so every loader treats a
missing file as "no data" and the product shows its honest empty states.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from config.settings import get_settings

logger = structlog.get_logger(__name__)


def reference_dir() -> Path:
    return Path(get_settings().reference_data_dir)


def reference_path(*parts: str) -> Path:
    return reference_dir().joinpath(*parts)


def read_yaml_file(path: Path) -> object | None:
    """The parsed YAML, or None (with one warning) when the file is absent."""
    if not path.is_file():
        logger.warning("reference_data_missing", path=str(path))
        return None
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


__all__ = ["read_yaml_file", "reference_dir", "reference_path"]
