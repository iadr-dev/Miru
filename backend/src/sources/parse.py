"""Value parsers shared by the TWSE / TPEx / TAIFEX / MOPS / TDCC source adapters."""

from __future__ import annotations


def to_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def optional_float(value: object) -> float | None:
    if value is None or str(value).strip().upper() == "N/A":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


__all__ = ["optional_float", "to_float"]
