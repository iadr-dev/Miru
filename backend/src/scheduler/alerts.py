"""Alert-condition evaluation over official/EOD data.

A rule's condition is evaluated against a `SourceResult` fetched through the same
capability/router path an interactive turn uses — never a direct `sources/` import. The output
is a caveated, non-trading observation ("2330 外資買超連 3 日"), phrased as something that
*happened*, never as an instruction to act.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict

from scheduler.models import AlertConditionType, AlertRule, Comparator
from schemas.source import HealthStatus, SourceResult


class AlertEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    triggered: bool
    message: str = ""


_NON_TRADING_SUFFIX = "此為資料觀察，非買賣建議。"


def _observed_value(condition_metric: str, result: SourceResult) -> float | None:
    """Pull the observed metric out of the payload. The payload shape is the source contract's;
    a missing metric means "cannot evaluate", surfaced as not-triggered rather than a crash."""
    bar = result.payload.get("bar")
    if isinstance(bar, dict):
        raw: object = cast("dict[str, object]", bar).get(condition_metric)
        if isinstance(raw, (int, float)):
            return float(raw)
    raw_top = result.payload.get(condition_metric)
    if isinstance(raw_top, (int, float)):
        return float(raw_top)
    return None


def _streak_length(condition_metric: str, result: SourceResult) -> int | None:
    """Length of the current consecutive-direction run for a series metric (e.g. 外資買超),
    read from a precomputed `{metric}_streak` field the canonical/derived payload carries."""
    raw = result.payload.get(f"{condition_metric}_streak")
    if isinstance(raw, int):
        return raw
    return None


def _threshold_hit(comparator: Comparator, observed: float, threshold: float) -> bool:
    if comparator in (Comparator.ABOVE, Comparator.CROSSES_ABOVE):
        return observed > threshold
    if comparator in (Comparator.BELOW, Comparator.CROSSES_BELOW):
        return observed < threshold
    return False


def evaluate_condition(alert: AlertRule, result: SourceResult) -> AlertEvaluation:
    """Evaluate `alert.condition` against `result`. A source that is unavailable/unreachable is
    never a trigger — an alert fires on observed data, never on the absence of it."""
    if result.health.status is HealthStatus.UNREACHABLE:
        return AlertEvaluation(triggered=False)

    condition = alert.condition
    if condition.comparator in (Comparator.STREAK_UP, Comparator.STREAK_DOWN):
        streak = _streak_length(condition.metric, result)
        if streak is None or streak < condition.threshold:
            return AlertEvaluation(triggered=False)
        direction = "買超" if condition.comparator is Comparator.STREAK_UP else "賣超"
        label = _metric_label(condition.type)
        return AlertEvaluation(
            triggered=True,
            message=(
                f"{alert.symbol} {label}{direction}連 {streak} 日"
                f"（門檻 {condition.threshold} 日）。{_NON_TRADING_SUFFIX}"
            ),
        )

    observed = _observed_value(condition.metric, result)
    if observed is None:
        return AlertEvaluation(triggered=False)
    if not _threshold_hit(condition.comparator, observed, float(condition.threshold)):
        return AlertEvaluation(triggered=False)
    above = condition.comparator in (Comparator.ABOVE, Comparator.CROSSES_ABOVE)
    relation = "高於" if above else "低於"
    return AlertEvaluation(
        triggered=True,
        message=(
            f"{alert.symbol} {condition.metric} 目前為 {observed:g}，{relation}設定值 "
            f"{condition.threshold}。{_NON_TRADING_SUFFIX}"
        ),
    )


def _metric_label(condition_type: AlertConditionType) -> str:
    if condition_type is AlertConditionType.INSTITUTIONAL:
        return "外資"
    return ""


__all__ = ["AlertEvaluation", "evaluate_condition"]
