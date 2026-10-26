"""`ChartHint` — an optional, declarative visualization hint a tool's `output_model` may carry
alongside its data. A minimal Vega-Lite-style `mark` + channel-encoding grammar, deliberately
small: 5 marks, ~6 channels, 4 field types — sized to exactly the chart shapes this app's tool
data actually needs (line/bar/area/scatter/pie), not the full Vega-Lite surface.

Two invariants that make this safe to add without touching governance:
1. `ChartHint` carries no color/hex value anywhere — `color`/`theta`/`x`/`y` are channel-to-field
   NAME mappings only. The frontend renderer resolves actual colors from design tokens
   (`--chart-1..10`/`--up`/`--down`), never from anything this model carries. An LLM (or a tool
   author) can pick WHICH field drives color, never what color it renders as.
2. Because `ChartHint` is just another field on a tool's existing `output_model`, it rides through
   `agent/loop.py::_build_card`'s existing `guardrails.scan_structured_payload(result.model_dump(
   mode="json"))` call for free — every string leaf here (chiefly `title`) is governance-scanned
   exactly like every other string in a tool's output, with zero new wiring.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldType(StrEnum):
    """The four Vega-Lite field types this grammar needs — drives axis/scale choice and the
    mark-compatibility rules below."""

    QUANTITATIVE = "quantitative"
    NOMINAL = "nominal"
    ORDINAL = "ordinal"
    TEMPORAL = "temporal"


class FieldDef(BaseModel):
    """One channel's mapping to a data field — never a value, always a field NAME the renderer
    looks up per-row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    type: FieldType
    aggregate: Literal["sum", "mean", "count"] | None = None


class Mark(StrEnum):
    """The 5 chart shapes this grammar covers. Waterfall/range-bar is `BAR` + `y2`, not a
    separate mark (see `ChartHint.y2`'s own docstring) — kept at 5 rather than 6 marks."""

    LINE = "line"
    BAR = "bar"
    AREA = "area"
    POINT = "point"  # scatter
    ARC = "arc"  # pie/donut


_TEMPORAL_OR_ORDINAL_MARKS = frozenset({Mark.LINE, Mark.AREA})
_DIVERGING_ALLOWED_MARKS = frozenset({Mark.BAR, Mark.LINE})


class ChartHint(BaseModel):
    """A declarative visualization hint: `mark` + channel encodings, resolved by the frontend
    renderer against the tool's own row data. Optional on every output model that carries one —
    absence means "no generic-chart opinion," not an error; the existing hardcoded `card_type`
    path still renders that tool's card exactly as it does today."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mark: Mark
    title: str | None = None
    # Names which of the tool's OWN row-list fields this hint plots — only needed when an
    # output_model has more than one array field (e.g. a composite tool bundling `events` +
    # `year_summaries` + `fill_results`), where "the first array field" would otherwise be
    # ambiguous. `None` means the frontend's existing single-row-list fallback applies unchanged.
    data_key: str | None = None
    x: FieldDef | None = None
    y: FieldDef | None = None
    # Only meaningful for `mark=BAR`: paired with `y` as a segment's [start, end], the running-
    # total shape a waterfall/range bar needs. Never populated for any other mark.
    y2: FieldDef | None = None
    color: FieldDef | None = None
    # Only meaningful for `mark=ARC` — replaces `x`/`y` entirely, since a pie/donut has no axes.
    theta: FieldDef | None = None
    # Only meaningful for `mark=POINT` (scatter) — an optional third quantitative channel.
    size: FieldDef | None = None
    # True routes the renderer to `--up`/`--down` (paired with a ▲/▼ + aria-label, matching this
    # app's existing `DivergingBarChart` convention) instead of a categorical `--chart-N` token —
    # never both; `color` and `diverging` are mutually exclusive (see validator below).
    diverging: bool = False

    @model_validator(mode="after")
    def _check_mark_field_compatibility(self) -> ChartHint:
        _check_axis_field(self)
        _check_arc_fields(self)
        _check_mark_specific_channels(self)
        _check_diverging(self)
        return self


def _check_axis_field(hint: ChartHint) -> None:
    if hint.mark not in _TEMPORAL_OR_ORDINAL_MARKS:
        return
    if hint.x is None:
        raise ValueError(f"mark={hint.mark.value} requires an x field")
    if hint.x.type not in (FieldType.TEMPORAL, FieldType.ORDINAL):
        raise ValueError(
            f"mark={hint.mark.value} requires x.type to be temporal or ordinal, "
            f"got {hint.x.type.value}"
        )


def _check_arc_fields(hint: ChartHint) -> None:
    if hint.mark is not Mark.ARC:
        if hint.theta is not None:
            raise ValueError(f"theta is only valid for mark=arc, got mark={hint.mark.value}")
        return
    if hint.x is not None or hint.y is not None:
        raise ValueError("mark=arc has no axes — x/y must not be set")
    if hint.theta is None or hint.color is None:
        raise ValueError("mark=arc requires both theta and color")


def _check_mark_specific_channels(hint: ChartHint) -> None:
    if hint.y2 is not None and hint.mark is not Mark.BAR:
        raise ValueError(f"y2 is only valid for mark=bar, got mark={hint.mark.value}")
    if hint.size is not None and hint.mark is not Mark.POINT:
        raise ValueError(f"size is only valid for mark=point, got mark={hint.mark.value}")


def _check_diverging(hint: ChartHint) -> None:
    if not hint.diverging:
        return
    if hint.mark not in _DIVERGING_ALLOWED_MARKS:
        allowed = sorted(m.value for m in _DIVERGING_ALLOWED_MARKS)
        got = hint.mark.value
        raise ValueError(f"diverging=True is only valid for mark in {allowed}, got {got}")
    if hint.color is not None:
        raise ValueError("diverging=True and color are mutually exclusive")


__all__ = ["ChartHint", "FieldDef", "FieldType", "Mark"]
