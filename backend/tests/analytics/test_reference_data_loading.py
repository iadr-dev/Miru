"""The supply-chain loader reads the private reference directory (synthetic data here), keeps the
first of a duplicated relationship, and treats missing files as "no data"."""

from __future__ import annotations

from pathlib import Path

import pytest

from analytics.themes.models import EdgeSource
from analytics.themes.research import load_concept_themes, load_supply_chain_seed
from config import private_data


def test_the_loader_reads_the_seed_and_every_relations_file_once() -> None:
    edges = load_supply_chain_seed()
    keys = [(e.from_symbol, e.to_symbol, e.edge_type.value) for e in edges]

    assert ("1111", "2222", "supplies") in keys  # from relations/batch-test.yaml
    assert ("2330", "AAPL", "supplies") in keys  # from seed.yaml
    assert len(keys) == len(set(keys))  # the duplicate row in the batch is dropped
    assert all(e.source is EdgeSource.SEED for e in edges)


def test_concept_themes_load_from_the_reference_directory() -> None:
    assert [spec.key for spec in load_concept_themes()] == ["test-theme"]


def test_a_missing_reference_directory_means_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(private_data, "reference_dir", lambda: tmp_path / "absent")

    assert load_supply_chain_seed() == []
    assert load_concept_themes() == []
