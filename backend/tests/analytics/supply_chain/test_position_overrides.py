"""The overrides file loader: missing = none, and clear errors naming file and row."""

from __future__ import annotations

from pathlib import Path

import pytest

from analytics.supply_chain.positions.models import Position
from analytics.supply_chain.positions.overrides import (
    OverrideFileError,
    load_overrides,
    parse_overrides,
)

GOOD = """
- {symbol: "9001", market: TW, chain_key: chain-a, position: upstream, reason: "invented"}
- {symbol: "9002", chain_key: chain-a, position: downstream, reason: "also invented"}
"""


def test_missing_file_means_no_overrides(tmp_path: Path) -> None:
    assert load_overrides(tmp_path / "nope.yaml") == []


def test_empty_file_means_no_overrides(tmp_path: Path) -> None:
    path = tmp_path / "o.yaml"
    path.write_text("", encoding="utf-8")
    assert load_overrides(path) == []


def test_loads_rows_with_default_market(tmp_path: Path) -> None:
    path = tmp_path / "o.yaml"
    path.write_text(GOOD, encoding="utf-8")
    rows = load_overrides(path)
    assert [(r.symbol, r.market, r.position) for r in rows] == [
        ("9001", "TW", Position.UPSTREAM),
        ("9002", "TW", Position.DOWNSTREAM),
    ]


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"symbol": "1"}, "expected a list"),
        ([{"symbol": "1", "chain_key": "c", "position": "sideways", "reason": "r"}], "row[0]"),
        ([{"symbol": "1", "chain_key": "c", "position": "upstream"}], "reason"),
        ([{"symbol": "1", "chain_key": "c", "position": "upstream", "reason": ""}], "reason"),
        ([{"symbol": 1, "chain_key": "c", "position": "upstream", "reason": "r"}], "symbol"),
        (
            [
                {"symbol": "1", "chain_key": "c", "position": "upstream", "reason": "r"},
                {"symbol": "1", "chain_key": "c", "position": "midstream", "reason": "r"},
            ],
            "duplicate",
        ),
        ([{"symbol": "1", "chain_key": "c", "position": "up", "reason": "r", "x": 1}], "row[0]"),
    ],
)
def test_bad_input_raises_a_clear_error(raw: object, fragment: str) -> None:
    with pytest.raises(OverrideFileError, match=r"position_overrides\.yaml") as excinfo:
        parse_overrides(raw)
    assert fragment in str(excinfo.value)


def test_unparseable_yaml_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "o.yaml"
    path.write_text("- {unclosed", encoding="utf-8")
    with pytest.raises(OverrideFileError, match=r"o\.yaml"):
        load_overrides(path)
