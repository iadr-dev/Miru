"""The researched supply-chain batches load beside the seed, and every row is sourced."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analytics.themes.models import EdgeSource, EdgeType
from analytics.themes.research import load_supply_chain_seed

_DIR = Path(__file__).resolve().parents[2] / "src" / "config" / "supply_chain_research"
_SYMBOL = re.compile(r"^(\d{4,5}|[A-Z]{1,5}(\.[A-Z])?)$")
_NEUTRAL_FORBIDDEN = re.compile(
    r"買進|賣出|目標價|建議|看好|\bbuy\b|\bsell\b|target price", re.IGNORECASE
)


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(_DIR.glob("*.yaml")):
        rows.extend(yaml.safe_load(path.read_text(encoding="utf-8")) or [])
    return rows


def test_every_researched_row_names_a_source_and_valid_ends() -> None:
    rows = _rows()
    assert rows, "the research folder should hold at least one batch"
    for row in rows:
        where = f"{row.get('from_symbol')}->{row.get('to_symbol')}"
        assert _SYMBOL.match(str(row["from_symbol"])), where
        assert _SYMBOL.match(str(row["to_symbol"])), where
        assert row["from_symbol"] != row["to_symbol"], where
        assert re.search(r"https?://", str(row.get("evidence", ""))), f"{where}: no source URL"
        assert not _NEUTRAL_FORBIDDEN.search(str(row["evidence"])), where


def test_the_loader_returns_seed_and_researched_edges_without_duplicates() -> None:
    edges = load_supply_chain_seed()
    keys = [(e.from_symbol, e.to_symbol, e.edge_type.value) for e in edges]

    assert len(keys) == len(set(keys))
    assert all(e.source is EdgeSource.SEED for e in edges)
    researched = {
        (str(r["from_symbol"]), str(r["to_symbol"]), str(r["edge_type"])) for r in _rows()
    }
    assert researched <= set(keys)
    assert any(e.edge_type is EdgeType.SUPPLIES for e in edges)
    # the hand-written seed is still there
    assert ("2330", "AAPL", "supplies") in keys
