"""Characterization of `ChartHint._check_mark_field_compatibility`: exact message and the order
in which overlapping violations are reported (the first failing check wins)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark


def _f(name: str, kind: FieldType) -> FieldDef:
    return FieldDef(field=name, type=kind)


_Q = FieldType.QUANTITATIVE
_N = FieldType.NOMINAL
_O = FieldType.ORDINAL
_T = FieldType.TEMPORAL


def _message(**kwargs: object) -> str:
    with pytest.raises(ValidationError) as info:
        ChartHint(**kwargs)  # type: ignore[arg-type]
    errors = info.value.errors()
    assert len(errors) == 1
    return str(errors[0]["msg"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"mark": Mark.LINE, "y": _f("v", _Q)},
            "Value error, mark=line requires an x field",
        ),
        (
            {"mark": Mark.AREA},
            "Value error, mark=area requires an x field",
        ),
        (
            {"mark": Mark.LINE, "x": _f("c", _N)},
            "Value error, mark=line requires x.type to be temporal or ordinal, got nominal",
        ),
        (
            {"mark": Mark.AREA, "x": _f("c", _Q)},
            "Value error, mark=area requires x.type to be temporal or ordinal, got quantitative",
        ),
        (
            {"mark": Mark.ARC, "x": _f("x", _Q), "theta": _f("a", _Q), "color": _f("c", _N)},
            "Value error, mark=arc has no axes — x/y must not be set",
        ),
        (
            {"mark": Mark.ARC, "y": _f("y", _Q), "theta": _f("a", _Q), "color": _f("c", _N)},
            "Value error, mark=arc has no axes — x/y must not be set",
        ),
        (
            {"mark": Mark.ARC, "theta": _f("a", _Q)},
            "Value error, mark=arc requires both theta and color",
        ),
        (
            {"mark": Mark.ARC, "color": _f("c", _N)},
            "Value error, mark=arc requires both theta and color",
        ),
        (
            {"mark": Mark.ARC},
            "Value error, mark=arc requires both theta and color",
        ),
        (
            {"mark": Mark.BAR, "theta": _f("t", _Q)},
            "Value error, theta is only valid for mark=arc, got mark=bar",
        ),
        (
            {"mark": Mark.POINT, "theta": _f("t", _Q)},
            "Value error, theta is only valid for mark=arc, got mark=point",
        ),
        (
            {"mark": Mark.LINE, "x": _f("x", _T), "y2": _f("y2", _Q)},
            "Value error, y2 is only valid for mark=bar, got mark=line",
        ),
        (
            {"mark": Mark.ARC, "theta": _f("a", _Q), "color": _f("c", _N), "y2": _f("y2", _Q)},
            "Value error, y2 is only valid for mark=bar, got mark=arc",
        ),
        (
            {"mark": Mark.BAR, "size": _f("s", _Q)},
            "Value error, size is only valid for mark=point, got mark=bar",
        ),
        (
            {"mark": Mark.LINE, "x": _f("x", _O), "size": _f("s", _Q)},
            "Value error, size is only valid for mark=point, got mark=line",
        ),
        (
            {"mark": Mark.ARC, "theta": _f("a", _Q), "color": _f("c", _N), "diverging": True},
            "Value error, diverging=True is only valid for mark in ['bar', 'line'], got arc",
        ),
        (
            {"mark": Mark.POINT, "diverging": True},
            "Value error, diverging=True is only valid for mark in ['bar', 'line'], got point",
        ),
        (
            {"mark": Mark.AREA, "x": _f("x", _T), "diverging": True},
            "Value error, diverging=True is only valid for mark in ['bar', 'line'], got area",
        ),
        (
            {"mark": Mark.BAR, "color": _f("c", _N), "diverging": True},
            "Value error, diverging=True and color are mutually exclusive",
        ),
        (
            {"mark": Mark.LINE, "x": _f("x", _T), "color": _f("c", _N), "diverging": True},
            "Value error, diverging=True and color are mutually exclusive",
        ),
    ],
)
def test_each_violation_reports_its_exact_message(kwargs: dict[str, object], message: str) -> None:
    assert _message(**kwargs) == message


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        # line x check precedes the theta / y2 / size / diverging checks
        (
            {"mark": Mark.LINE, "theta": _f("t", _Q), "y2": _f("y2", _Q), "diverging": True},
            "Value error, mark=line requires an x field",
        ),
        (
            {"mark": Mark.AREA, "x": _f("x", _N), "theta": _f("t", _Q)},
            "Value error, mark=area requires x.type to be temporal or ordinal, got nominal",
        ),
        # theta (non-arc) precedes y2, size and diverging
        (
            {"mark": Mark.BAR, "theta": _f("t", _Q), "y2": _f("y2", _Q), "size": _f("s", _Q)},
            "Value error, theta is only valid for mark=arc, got mark=bar",
        ),
        # arc axes check precedes the theta/color requirement
        (
            {"mark": Mark.ARC, "x": _f("x", _Q)},
            "Value error, mark=arc has no axes — x/y must not be set",
        ),
        # arc requirement precedes y2 / diverging
        (
            {"mark": Mark.ARC, "y2": _f("y2", _Q), "diverging": True},
            "Value error, mark=arc requires both theta and color",
        ),
        # y2 precedes size, size precedes diverging
        (
            {"mark": Mark.POINT, "y2": _f("y2", _Q), "size": _f("s", _Q)},
            "Value error, y2 is only valid for mark=bar, got mark=point",
        ),
        (
            {"mark": Mark.BAR, "size": _f("s", _Q), "diverging": True, "color": _f("c", _N)},
            "Value error, size is only valid for mark=point, got mark=bar",
        ),
        # unsupported diverging mark precedes the colour exclusivity check
        (
            {"mark": Mark.POINT, "diverging": True, "color": _f("c", _N)},
            "Value error, diverging=True is only valid for mark in ['bar', 'line'], got point",
        ),
    ],
)
def test_overlapping_violations_report_the_first_check_only(
    kwargs: dict[str, object], message: str
) -> None:
    assert _message(**kwargs) == message


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mark": Mark.BAR},
        {"mark": Mark.BAR, "color": _f("c", _N)},
        {"mark": Mark.POINT},
        {"mark": Mark.LINE, "x": _f("x", _O)},
        {"mark": Mark.AREA, "x": _f("x", _T), "color": _f("c", _N)},
        {"mark": Mark.LINE, "x": _f("x", _T), "diverging": True},
        {"mark": Mark.BAR, "diverging": True},
        {"mark": Mark.ARC, "theta": _f("a", _Q), "color": _f("c", _N)},
        {"mark": Mark.POINT, "x": _f("x", _Q), "y": _f("y", _Q), "size": _f("s", _Q)},
        {"mark": Mark.BAR, "y": _f("a", _Q), "y2": _f("b", _Q), "x": _f("s", _N)},
    ],
)
def test_valid_combinations_are_accepted(kwargs: dict[str, object]) -> None:
    hint = ChartHint(**kwargs)  # type: ignore[arg-type]
    assert hint.mark is kwargs["mark"]
