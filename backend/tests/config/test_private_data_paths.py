"""`safe_reference_file`: the one gate for a caller-supplied path read from the reference data."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.private_data import YAML_SUFFIXES, UnsafeReferencePathError, safe_reference_file


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "ref"
    path.mkdir()
    return path


def test_valid_file_inside_returns_the_resolved_path(root: Path) -> None:
    inside = root / "a.yaml"
    inside.write_text("[]", encoding="utf-8")
    assert safe_reference_file(root, inside, YAML_SUFFIXES) == inside.resolve()


def test_suffix_match_is_case_insensitive(root: Path) -> None:
    inside = root / "a.YML"
    inside.write_text("[]", encoding="utf-8")
    assert safe_reference_file(root, inside, YAML_SUFFIXES) == inside.resolve()


def test_outside_path_is_rejected(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "o.yaml"
    outside.write_text("[]", encoding="utf-8")
    with pytest.raises(UnsafeReferencePathError):
        safe_reference_file(root, outside, YAML_SUFFIXES)


def test_parent_traversal_is_rejected(root: Path, tmp_path: Path) -> None:
    (tmp_path / "o.yaml").write_text("[]", encoding="utf-8")
    with pytest.raises(UnsafeReferencePathError):
        safe_reference_file(root, root / ".." / "o.yaml", YAML_SUFFIXES)


def test_symlink_escape_is_rejected(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "o.yaml"
    outside.write_text("[]", encoding="utf-8")
    link = root / "link.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    with pytest.raises(UnsafeReferencePathError):
        safe_reference_file(root, link, YAML_SUFFIXES)


def test_wrong_suffix_missing_file_and_directory_are_rejected(root: Path) -> None:
    wrong = root / "a.txt"
    wrong.write_text("[]", encoding="utf-8")
    for bad in (wrong, root / "nope.yaml", root):
        with pytest.raises(UnsafeReferencePathError):
            safe_reference_file(root, bad, YAML_SUFFIXES)
