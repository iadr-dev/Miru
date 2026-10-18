"""Evaluation metrics (pure).

For a golden label whose (symbol, chain) has a stored result the classifier answered; otherwise it
abstained.

- coverage      answered / all labels
- accuracy      correct answers / answered, over `sure` labels only (None when there are none)
- per position  precision = TP / answered-as-p, recall = TP / labelled-p (an abstention is a miss
                for recall), F1 = harmonic mean; over `sure` labels
- confusion     expected -> got (including "abstain"), over `sure` labels
- disagreements every answered label (sure or unsure) whose answer differs from the label

The gate (`check_gate`) is the CI rule: accuracy >= 85% on answered sure rows, abstentions <= 25%.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.evaluation.golden import GoldenLabel, LabelConfidence
from analytics.supply_chain.positions.models import ChainPosition, Position, Reason

ABSTAIN = "abstain"
MIN_ACCURACY = 0.85
MAX_ABSTAIN_RATE = 0.25


class PositionScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    precision: float | None
    recall: float | None
    f1: float | None
    support: int


class Disagreement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    chain_key: str
    expected: Position
    got: Position
    label_confidence: LabelConfidence
    reasons: tuple[Reason, ...]


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    answered: int
    sure_total: int
    sure_answered: int
    sure_correct: int
    coverage: float | None
    accuracy: float | None
    per_position: dict[str, PositionScore]
    confusion: dict[str, dict[str, int]]
    disagreements: tuple[Disagreement, ...]


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def evaluate(golden: Iterable[GoldenLabel], predictions: Iterable[ChainPosition]) -> EvalReport:
    predicted: Mapping[tuple[str, str], ChainPosition] = {
        (p.symbol, p.chain_key): p for p in predictions
    }
    labels = list(golden)
    names = [p.value for p in Position]
    confusion = {e: dict.fromkeys([*names, ABSTAIN], 0) for e in names}
    answered = sure_total = sure_answered = sure_correct = 0
    disagreements: list[Disagreement] = []
    for label in labels:
        got = predicted.get((label.symbol, label.chain_key))
        is_sure = label.label_confidence is LabelConfidence.SURE
        if got is not None:
            answered += 1
            if got.position is not label.position:
                disagreements.append(
                    Disagreement(
                        symbol=label.symbol,
                        chain_key=label.chain_key,
                        expected=label.position,
                        got=got.position,
                        label_confidence=label.label_confidence,
                        reasons=got.reasons,
                    )
                )
        if not is_sure:
            continue
        sure_total += 1
        confusion[label.position.value][got.position.value if got else ABSTAIN] += 1
        if got is not None:
            sure_answered += 1
            sure_correct += got.position is label.position
    per_position: dict[str, PositionScore] = {}
    for name in names:
        true_pos = confusion[name][name]
        answered_as = sum(confusion[e][name] for e in names)
        labelled = sum(confusion[name].values())
        precision = _ratio(true_pos, answered_as)
        recall = _ratio(true_pos, labelled)
        per_position[name] = PositionScore(
            precision=precision, recall=recall, f1=_f1(precision, recall), support=labelled
        )
    return EvalReport(
        total=len(labels),
        answered=answered,
        sure_total=sure_total,
        sure_answered=sure_answered,
        sure_correct=sure_correct,
        coverage=_ratio(answered, len(labels)),
        accuracy=_ratio(sure_correct, sure_answered),
        per_position=per_position,
        confusion=confusion,
        disagreements=tuple(disagreements),
    )


def check_gate(
    report: EvalReport,
    *,
    min_accuracy: float = MIN_ACCURACY,
    max_abstain_rate: float = MAX_ABSTAIN_RATE,
) -> list[str]:
    """Why the report fails the gate (empty = passes). An empty golden set fails."""
    if report.total == 0 or report.coverage is None:
        return ["golden set is empty"]
    failures: list[str] = []
    abstain_rate = 1.0 - report.coverage
    if abstain_rate > max_abstain_rate:
        failures.append(f"abstain rate {abstain_rate:.1%} is above {max_abstain_rate:.0%}")
    if report.accuracy is None:
        failures.append("no answered 'sure' rows to measure accuracy on")
    elif report.accuracy < min_accuracy:
        failures.append(f"accuracy {report.accuracy:.1%} is below {min_accuracy:.0%}")
    return failures


def format_report(report: EvalReport) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    lines = [
        f"labels {report.total}  answered {report.answered}  coverage {pct(report.coverage)}",
        f"sure labels {report.sure_total}  answered {report.sure_answered}  "
        f"correct {report.sure_correct}  accuracy {pct(report.accuracy)}",
        "",
        "position      precision  recall  f1      support",
    ]
    for name, score in report.per_position.items():
        lines.append(
            f"{name:<13} {pct(score.precision):<10} {pct(score.recall):<7} "
            f"{pct(score.f1):<7} {score.support}"
        )
    lines += ["", "confusion (rows = expected, columns = got)"]
    columns = [p.value for p in Position] + [ABSTAIN]
    lines.append(f"{'':<13} " + "  ".join(f"{c:>10}" for c in columns))
    for expected, row in report.confusion.items():
        lines.append(f"{expected:<13} " + "  ".join(f"{row[c]:>10}" for c in columns))
    lines += ["", f"disagreements ({len(report.disagreements)})"]
    for d in report.disagreements:
        codes = ",".join(r.code.value for r in d.reasons)
        lines.append(
            f"  {d.symbol} {d.chain_key}: expected {d.expected.value}, got {d.got.value} "
            f"[{d.label_confidence.value}] ({codes})"
        )
    return "\n".join(lines)


__all__ = [
    "ABSTAIN",
    "MAX_ABSTAIN_RATE",
    "MIN_ACCURACY",
    "Disagreement",
    "EvalReport",
    "PositionScore",
    "check_gate",
    "evaluate",
    "format_report",
]
