"""`ChartHint` — the mark/field-type compatibility validator (`schemas/chart_hint.py`). Every
invalid combination this validator rejects is a case a bad `chart_hint` must never reach the
frontend for (a discriminated-union rejection at construction time, per the plan's Tier-2
hardening requirement), not just a documentation comment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark


def test_bar_with_ordinal_x_and_quantitative_y_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.BAR,
        x=FieldDef(field="month", type=FieldType.ORDINAL),
        y=FieldDef(field="revenue", type=FieldType.QUANTITATIVE),
    )
    assert hint.mark is Mark.BAR


def test_line_with_temporal_x_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.LINE,
        x=FieldDef(field="trade_date", type=FieldType.TEMPORAL),
        y=FieldDef(field="close", type=FieldType.QUANTITATIVE),
    )
    assert hint.mark is Mark.LINE


def test_arc_with_theta_and_color_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.ARC,
        theta=FieldDef(field="amount", type=FieldType.QUANTITATIVE),
        color=FieldDef(field="year", type=FieldType.NOMINAL),
    )
    assert hint.mark is Mark.ARC


def test_diverging_bar_without_color_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.BAR,
        x=FieldDef(field="investor", type=FieldType.NOMINAL),
        y=FieldDef(field="net", type=FieldType.QUANTITATIVE),
        diverging=True,
    )
    assert hint.diverging is True


def test_point_with_size_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.POINT,
        x=FieldDef(field="momentum", type=FieldType.QUANTITATIVE),
        y=FieldDef(field="strength", type=FieldType.QUANTITATIVE),
        size=FieldDef(field="volume", type=FieldType.QUANTITATIVE),
    )
    assert hint.size is not None


def test_bar_with_y2_is_valid() -> None:
    hint = ChartHint(
        mark=Mark.BAR,
        x=FieldDef(field="stage", type=FieldType.ORDINAL),
        y=FieldDef(field="start", type=FieldType.QUANTITATIVE),
        y2=FieldDef(field="end", type=FieldType.QUANTITATIVE),
    )
    assert hint.y2 is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        # line requires a temporal/ordinal x, not nominal.
        {
            "mark": Mark.LINE,
            "x": FieldDef(field="category", type=FieldType.NOMINAL),
            "y": FieldDef(field="v", type=FieldType.QUANTITATIVE),
        },
        # area requires an x at all.
        {"mark": Mark.AREA, "y": FieldDef(field="v", type=FieldType.QUANTITATIVE)},
        # arc forbids x/y — theta/color replace them entirely.
        {
            "mark": Mark.ARC,
            "x": FieldDef(field="x", type=FieldType.QUANTITATIVE),
            "theta": FieldDef(field="a", type=FieldType.QUANTITATIVE),
            "color": FieldDef(field="c", type=FieldType.NOMINAL),
        },
        # arc requires both theta and color, not just one.
        {"mark": Mark.ARC, "theta": FieldDef(field="a", type=FieldType.QUANTITATIVE)},
        # theta is arc-only.
        {
            "mark": Mark.BAR,
            "x": FieldDef(field="x", type=FieldType.ORDINAL),
            "y": FieldDef(field="y", type=FieldType.QUANTITATIVE),
            "theta": FieldDef(field="t", type=FieldType.QUANTITATIVE),
        },
        # y2 is bar-only.
        {
            "mark": Mark.LINE,
            "x": FieldDef(field="x", type=FieldType.TEMPORAL),
            "y": FieldDef(field="y", type=FieldType.QUANTITATIVE),
            "y2": FieldDef(field="y2", type=FieldType.QUANTITATIVE),
        },
        # size is point-only.
        {
            "mark": Mark.BAR,
            "x": FieldDef(field="x", type=FieldType.ORDINAL),
            "y": FieldDef(field="y", type=FieldType.QUANTITATIVE),
            "size": FieldDef(field="s", type=FieldType.QUANTITATIVE),
        },
        # diverging is only valid for bar/line, not arc.
        {
            "mark": Mark.ARC,
            "theta": FieldDef(field="a", type=FieldType.QUANTITATIVE),
            "color": FieldDef(field="c", type=FieldType.NOMINAL),
            "diverging": True,
        },
        # diverging and color are mutually exclusive.
        {
            "mark": Mark.BAR,
            "x": FieldDef(field="x", type=FieldType.ORDINAL),
            "y": FieldDef(field="y", type=FieldType.QUANTITATIVE),
            "color": FieldDef(field="c", type=FieldType.NOMINAL),
            "diverging": True,
        },
    ],
)
def test_incompatible_mark_field_combinations_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChartHint(**kwargs)  # type: ignore[arg-type]


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        ChartHint(mark=Mark.BAR, bogus="x")  # type: ignore[call-arg]


def test_field_def_carries_no_color_value() -> None:
    """`FieldDef` is a channel-to-field-NAME mapping only — asserting the model has no color/hex
    field at all is the one test that keeps this schema honest to its own "never an LLM-chosen
    color" design invariant as the model evolves."""
    assert set(FieldDef.model_fields) == {"field", "type", "aggregate"}
