"""Assembles the full FinMind `_DATASETS` registry from each dataset-category module's own
partial registry.

This is the one place in the package that needs to know about every category module — each
category module stays independently readable and testable, and adding a new category is a
one-line merge here rather than a change to `_adapter.py`.
"""

from __future__ import annotations

from schemas.enums import Capability

from . import (
    chip_institutional,
    convertible_bond,
    derivative,
    fundamental,
    global_economic,
    international,
    others,
    realtime,
    technical,
)
from ._shared import _DatasetConfig

_DATASETS: dict[Capability, _DatasetConfig] = {
    **technical.DATASETS,
    **chip_institutional.DATASETS,
    **fundamental.DATASETS,
    **derivative.DATASETS,
    **realtime.DATASETS,
    **convertible_bond.DATASETS,
    **others.DATASETS,
    **international.DATASETS,
    **global_economic.DATASETS,
}

__all__ = ["_DATASETS"]
