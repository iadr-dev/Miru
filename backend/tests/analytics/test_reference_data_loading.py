"""The concept-theme loader reads the private reference directory (synthetic data here) and treats
missing files as "no data"."""

from __future__ import annotations

from pathlib import Path

import pytest

from analytics.themes.research import load_concept_themes
from config import private_data


def test_concept_themes_load_from_the_reference_directory() -> None:
    assert [spec.key for spec in load_concept_themes()] == ["test-theme"]


def test_a_missing_reference_directory_means_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(private_data, "reference_dir", lambda: tmp_path / "absent")

    assert load_concept_themes() == []
