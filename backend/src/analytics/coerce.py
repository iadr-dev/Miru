"""Loose-value coercions shared by the scoring and screener batches."""

from __future__ import annotations


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


__all__ = ["as_float", "as_int"]
