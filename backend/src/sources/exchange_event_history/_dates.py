"""Republic-of-China dates as the exchanges print them in the attention / disposition reports."""

from __future__ import annotations

import re
from datetime import date as date_
from typing import Final

_ROC_OFFSET: Final = 1911
# '113/01/11' (disposition lists) and '113.01.18' (TWSE attention list): year, month, day.
_ROC_DATE: Final = re.compile(r"^\s*(\d{2,3})[./](\d{1,2})[./](\d{1,2})\s*$")
# A period is two such dates joined by '~', the full-width tilde U+FF5E, or a dash.
_FULLWIDTH_TILDE: Final = chr(0xFF5E)
_PERIOD_SEPARATORS: Final = re.compile(rf"\s*[~{_FULLWIDTH_TILDE}-]\s*")


def parse_roc_date(text: object) -> date_ | None:
    """`'113/01/11'` or `'113.01.18'` -> 2024-01-11 / 2024-01-18; anything else (blank, `-----`,
    an impossible calendar day) -> None."""
    found = _ROC_DATE.match(str(text))
    if found is None:
        return None
    try:
        return date_(int(found[1]) + _ROC_OFFSET, int(found[2]), int(found[3]))
    except ValueError:
        return None


def parse_roc_period(text: object) -> tuple[date_, date_] | None:
    """`'113/01/12~113/01/29'` (or with U+FF5E) -> (2024-01-12, 2024-01-29), or None when either
    end is not a date or the period runs backwards."""
    parts = _PERIOD_SEPARATORS.split(str(text).strip())
    if len(parts) != 2:  # noqa: PLR2004 - a period has a start and an end
        return None
    start, end = parse_roc_date(parts[0]), parse_roc_date(parts[1])
    if start is None or end is None or end < start:
        return None
    return start, end
