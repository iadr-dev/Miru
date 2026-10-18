"""Evaluation: golden parsing, the metric arithmetic, and the accuracy gate on a synthetic chain.
Everything here is invented; no real labels or companies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analytics.supply_chain.evaluation.golden import (
    GoldenFileError,
    GoldenLabel,
    LabelConfidence,
    load_golden,
    parse_golden,
)
from analytics.supply_chain.evaluation.metrics import (
    ABSTAIN,
    check_gate,
    evaluate,
    format_report,
)
from analytics.supply_chain.positions.models import (
    ChainMember,
    ChainPosition,
    ChainSpec,
    Method,
    Position,
    Prior,
    PriorSource,
    Reason,
    ReasonCode,
)
from analytics.supply_chain.positions.resolver import resolve_positions

UP, MID, DOWN = Position.UPSTREAM, Position.MIDSTREAM, Position.DOWNSTREAM


def _label(
    symbol: str, position: Position, conf: LabelConfidence = LabelConfidence.SURE
) -> GoldenLabel:
    return GoldenLabel(symbol=symbol, chain_key="c", position=position, label_confidence=conf)


def _pred(symbol: str, position: Position) -> ChainPosition:
    return ChainPosition(
        symbol=symbol,
        chain_key="c",
        position=position,
        method=Method.PRIOR,
        confidence=0.8,
        reasons=(Reason(code=ReasonCode.PRIOR_CURATED, detail="d"),),
    )


def test_metric_math() -> None:
    golden = [
        _label("1", UP),
        _label("2", UP),
        _label("3", MID),
        _label("4", DOWN),
        _label("5", DOWN),  # abstained
        _label("6", UP, LabelConfidence.UNSURE),  # answered wrongly, but unsure
    ]
    predictions = [
        _pred("1", UP),  # correct
        _pred("2", MID),  # wrong
        _pred("3", MID),  # correct
        _pred("4", DOWN),  # correct
        _pred("6", DOWN),
        _pred("9", UP),  # not in the golden set: ignored
    ]
    report = evaluate(golden, predictions)
    assert (report.total, report.answered) == (6, 5)
    assert report.coverage == pytest.approx(5 / 6)
    assert (report.sure_total, report.sure_answered, report.sure_correct) == (5, 4, 3)
    assert report.accuracy == pytest.approx(3 / 4)
    up, mid, down = (report.per_position[p.value] for p in (UP, MID, DOWN))
    assert (up.precision, up.recall, up.support) == (1.0, 0.5, 2)  # 1 of 1 right; 1 of 2 found
    assert mid.precision == pytest.approx(0.5)  # answered mid twice, one was right
    assert mid.recall == 1.0
    assert mid.f1 == pytest.approx(2 * 0.5 * 1.0 / 1.5)
    assert (down.precision, down.recall) == (1.0, 0.5)  # the abstention is a recall miss
    assert report.confusion["upstream"] == {
        "upstream": 1,
        "midstream": 1,
        "downstream": 0,
        ABSTAIN: 0,
    }
    assert report.confusion["downstream"][ABSTAIN] == 1
    assert {(d.symbol, d.expected, d.got, d.label_confidence) for d in report.disagreements} == {
        ("2", UP, MID, LabelConfidence.SURE),
        ("6", UP, DOWN, LabelConfidence.UNSURE),
    }
    assert report.disagreements[0].reasons[0].code is ReasonCode.PRIOR_CURATED


def test_empty_and_degenerate_inputs_do_not_divide_by_zero() -> None:
    empty = evaluate([], [])
    assert (empty.coverage, empty.accuracy) == (None, None)
    assert check_gate(empty) == ["golden set is empty"]
    nothing_answered = evaluate([_label("1", UP)], [])
    assert (nothing_answered.coverage, nothing_answered.accuracy) == (0.0, None)
    assert nothing_answered.per_position["upstream"].precision is None
    assert nothing_answered.per_position["upstream"].f1 is None


def test_unsure_rows_do_not_count_towards_accuracy() -> None:
    report = evaluate([_label("1", UP, LabelConfidence.UNSURE)], [_pred("1", DOWN)])
    assert report.accuracy is None
    assert len(report.disagreements) == 1


def test_gate_thresholds() -> None:
    ok = evaluate([_label(str(i), UP) for i in range(10)], [_pred(str(i), UP) for i in range(10)])
    assert check_gate(ok) == []
    inaccurate = evaluate(
        [_label(str(i), UP) for i in range(10)],
        [_pred(str(i), UP if i < 8 else DOWN) for i in range(10)],
    )
    assert any("accuracy" in f for f in check_gate(inaccurate))  # 80% < 85%
    abstains = evaluate(
        [_label(str(i), UP) for i in range(10)], [_pred(str(i), UP) for i in range(7)]
    )
    assert any("abstain" in f for f in check_gate(abstains))  # 30% > 25%
    assert (
        check_gate(
            evaluate([_label(str(i), UP) for i in range(4)], [_pred(str(i), UP) for i in range(3)])
        )
        == []
    )  # exactly 25% abstained is allowed


def _synthetic_chain() -> tuple[ChainSpec, list[tuple[str, str]], list[GoldenLabel]]:
    """Ten invented companies: a curated tier map plus supply edges, with a golden label each."""
    members = {
        "1": "ore", "2": "ore", "3": "parts", "4": "parts", "5": "device",
        "6": "device", "7": "retail", "8": "retail", "9": "unmapped", "10": "unmapped",
    }  # fmt: skip
    chain = ChainSpec(
        chain_key="c",
        chain_name="Chain C",
        members=tuple(ChainMember(symbol=s, sub_industry=sub) for s, sub in members.items()),
        tiers={
            "ore": Prior(position=UP, source=PriorSource.CURATED),
            "parts": Prior(position=MID, source=PriorSource.CURATED),
            "device": Prior(position=MID, source=PriorSource.CURATED),
            "retail": Prior(position=DOWN, source=PriorSource.CURATED),
        },
    )
    # 9 and 10 have no prior; their edges place them: 1 -> 9 -> 7 ... and 2 -> 10 -> 8, 3 -> 10.
    edges = [("1", "9"), ("9", "7"), ("2", "10"), ("10", "8"), ("3", "10")]
    expected = {
        "1": UP, "2": UP, "3": MID, "4": MID, "5": MID, "6": MID,
        "7": DOWN, "8": DOWN, "9": MID, "10": MID,
    }  # fmt: skip
    golden = [_label(s, p) for s, p in expected.items()]
    return chain, edges, golden


def test_accuracy_gate_passes_on_a_synthetic_chain() -> None:
    chain, edges, golden = _synthetic_chain()
    report = evaluate(golden, resolve_positions([chain], edges))
    assert report.coverage == 1.0
    assert report.accuracy == 1.0
    assert check_gate(report) == []


def test_accuracy_gate_fails_when_the_classifier_regresses() -> None:
    chain, _edges, golden = _synthetic_chain()
    broken = chain.model_copy(
        update={
            "tiers": {
                "ore": Prior(position=DOWN, source=PriorSource.CURATED),
                "parts": Prior(position=UP, source=PriorSource.CURATED),
                "device": Prior(position=UP, source=PriorSource.CURATED),
                "retail": Prior(position=UP, source=PriorSource.CURATED),
            }
        }
    )
    report = evaluate(golden, resolve_positions([broken], []))
    assert report.accuracy is not None and report.accuracy < 0.85
    assert check_gate(report)


def test_report_text_lists_disagreements_and_the_matrix() -> None:
    report = evaluate([_label("1", UP)], [_pred("1", DOWN)])
    text = format_report(report)
    assert "accuracy 0.0%" in text
    assert "1 c: expected upstream, got downstream" in text
    assert "confusion" in text


def test_parse_golden_defaults_and_errors(tmp_path: Path) -> None:
    rows = parse_golden([{"symbol": "1", "chain_key": "c", "position": "upstream"}])
    assert rows[0].label_confidence is LabelConfidence.SURE
    for bad in (
        {"symbol": "1"},
        [{"symbol": "1", "chain_key": "c", "position": "left"}],
        [{"symbol": "1", "chain_key": "c", "position": "upstream", "label_confidence": "maybe"}],
        [{"symbol": "1", "chain_key": "c", "position": "upstream"}] * 2,
    ):
        with pytest.raises(GoldenFileError):
            parse_golden(bad)
    with pytest.raises(GoldenFileError, match="not found"):
        load_golden(tmp_path / "missing.yaml")
    path = tmp_path / "g.yaml"
    path.write_text(
        '- {symbol: "1", chain_key: c, position: midstream, label_confidence: unsure, note: n}\n',
        encoding="utf-8",
    )
    assert load_golden(path)[0].label_confidence is LabelConfidence.UNSURE


def test_report_serialises_to_json() -> None:
    report = evaluate([_label("1", UP)], [_pred("1", UP)])
    assert json.loads(report.model_dump_json())["accuracy"] == 1.0
