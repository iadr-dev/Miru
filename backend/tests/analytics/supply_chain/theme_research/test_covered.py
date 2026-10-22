"""Step C coverage check: a candidate whose VERIFIED members already sit in one existing concept
theme is not proposed as a new theme. Synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from analytics.supply_chain.theme_research.files import covered_path
from analytics.supply_chain.theme_research.job import execute_verify, format_verify
from analytics.supply_chain.theme_research.models import VerifySummary
from analytics.supply_chain.theme_research.proposals import proposal_paths
from analytics.supply_chain.themes_io.promote import IndustryRoster, run_promote
from ingest.store import ParquetStore
from tests.analytics.supply_chain.theme_research.support import (
    CANDIDATE_ID,
    CANDIDATE_LABEL,
    COMPANIES,
    THEME_KEY,
    THEME_ZH,
    TODAY,
    candidate_claim,
    directory,
    make_root,
    seed_claims,
    settings,
)
from tests.analytics.supply_chain.theme_research.test_verify import members_rows, verify

ALL = [f"91{n:02d}" for n in range(1, 13)]


def hold(root: Path, symbols: list[str]) -> None:
    """Make the demo concept theme hold exactly `symbols`."""
    path = root / "themes" / "concepts" / f"{THEME_KEY}.yaml"
    theme: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    theme["members"] = [
        {"symbol": s, "name": COMPANIES[s][0], "position": "midstream", "origin": "seeded"}
        for s in symbols
    ]
    path.write_text(yaml.safe_dump(theme, allow_unicode=True, sort_keys=False), encoding="utf-8")


def candidates(root: Path, symbols: list[str]) -> None:
    seed_claims(root, [candidate_claim(s) for s in symbols])


def covered_lines(root: Path) -> list[dict[str, Any]]:
    path = covered_path(root)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def new_theme_rows(root: Path) -> list[dict[str, Any]]:
    path = proposal_paths(root, TODAY).new_themes
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []


async def test_a_fully_covered_candidate_proposes_nothing_and_is_recorded(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:7])
    candidates(root, ALL[:7])
    summary = await verify(root, tmp_path)
    assert (summary.verified, summary.covered, summary.new_themes, summary.member_rows) == (
        7,
        1,
        0,
        0,
    )
    assert new_theme_rows(root) == []
    assert members_rows(root) == []
    [entry] = covered_lines(root)
    assert (entry["candidate"], entry["covering"]) == (CANDIDATE_ID, THEME_KEY)
    assert (entry["containment"], entry["verified"], entry["new"]) == (1.0, 7, 0)
    assert "covered=1" in summary.line()
    assert summary.notes == [f"candidate {CANDIDATE_ID}: covered by {THEME_KEY} (7/7)"]


async def test_a_mostly_covered_candidate_adds_only_its_new_members(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:10])
    candidates(root, ALL)
    summary = await verify(root, tmp_path)
    assert (summary.covered, summary.new_themes, summary.member_rows) == (1, 0, 2)
    assert new_theme_rows(root) == []
    rows = members_rows(root)
    assert {r["symbol"] for r in rows} == {"9111", "9112"}
    assert {r["theme_key"] for r in rows} == {THEME_KEY}
    assert set(rows[0]) == {
        "theme_key",
        "symbol",
        "name",
        "position",
        "publisher",
        "url",
        "excerpt",
        "confidence",
    }
    [entry] = covered_lines(root)
    assert (entry["verified"], entry["new"], entry["containment"]) == (12, 2, 0.8333)


async def test_the_intermediate_band_still_proposes_the_theme_with_a_related_key(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:5])
    candidates(root, ALL[:8])
    summary = await verify(root, tmp_path)
    assert (summary.covered, summary.new_themes, summary.member_rows) == (0, 1, 8)
    [theme] = new_theme_rows(root)
    assert theme["related"] == [THEME_KEY]
    assert len(theme["members"]) == 8
    assert summary.notes == [f"candidate {CANDIDATE_ID}: partly covered by {THEME_KEY} (5/8)"]
    assert covered_lines(root) == []


async def test_a_candidate_below_the_band_is_proposed_as_before(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:3])
    candidates(root, ALL[:8])
    summary = await verify(root, tmp_path)
    assert (summary.covered, summary.new_themes, summary.notes) == (0, 1, [])
    [theme] = new_theme_rows(root)
    assert theme["related"] == []


async def test_the_threshold_can_be_overridden(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:6])
    candidates(root, ALL[:8])
    strict = await verify(root, tmp_path, dry_run=True)
    assert (strict.covered, strict.new_themes) == (0, 1)
    loose = await execute_verify(
        settings(),
        ParquetStore(tmp_path / "parquet"),  # type: ignore[arg-type]
        today=TODAY,
        limit=None,
        dry_run=False,
        directory=directory(),
        root=root,
        covered_threshold=0.7,
    )
    assert (loose.covered, loose.new_themes, loose.member_rows) == (1, 0, 2)


async def test_a_rerun_changes_nothing_and_a_covered_candidate_is_not_judged_again(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:10])
    candidates(root, ALL)
    await verify(root, tmp_path)
    paths = proposal_paths(root, TODAY)
    before = (
        paths.members.read_text(encoding="utf-8"),
        covered_path(root).read_text(encoding="utf-8"),
    )
    again = await verify(root, tmp_path)
    assert again.groups == 0
    assert again.covered == 0
    assert again.member_rows == 0
    assert again.reasons == {"already_covered": 12}
    assert (
        paths.members.read_text(encoding="utf-8"),
        covered_path(root).read_text(encoding="utf-8"),
    ) == before
    assert not paths.new_themes.exists()


async def test_a_covered_candidate_stays_covered_when_one_more_member_arrives(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:7])
    candidates(root, ALL[:7])
    await verify(root, tmp_path)
    hold(root, ALL[:7])
    seed_claims(root, [candidate_claim("9112")])
    summary = await verify(root, tmp_path)
    assert (summary.groups, summary.covered, summary.new_themes) == (1, 1, 0)
    assert [r["symbol"] for r in members_rows(root)] == ["9112"]
    assert covered_lines(root)[-1]["verified"] == 8
    assert covered_lines(root)[-1]["new"] == 1


async def test_a_candidate_that_gains_outside_members_is_proposed_again(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:4])
    candidates(root, ALL[:4])
    first = await verify(root, tmp_path)
    assert first.covered == 1
    seed_claims(root, [candidate_claim(s) for s in ALL[4:10]])
    second = await verify(root, tmp_path)
    assert (second.covered, second.new_themes) == (0, 1)
    [theme] = new_theme_rows(root)
    assert {m["symbol"] for m in theme["members"]} == set(ALL[4:10])


async def test_console_output_names_the_candidate_and_theme_by_id_only(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:7])
    candidates(root, ALL[:7])
    out = format_verify(await verify(root, tmp_path))
    assert f"candidate {CANDIDATE_ID}: covered by {THEME_KEY} (7/7)" in out
    for name in (*[COMPANIES[s][0] for s in ALL[:7]], THEME_ZH, CANDIDATE_LABEL):
        assert name not in out
    assert "http" not in out
    for zh, en in COMPANIES.values():
        text = covered_path(root).read_text(encoding="utf-8")
        assert zh not in text
        assert en not in text


async def test_dry_run_records_nothing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:7])
    candidates(root, ALL[:7])
    summary: VerifySummary = await verify(root, tmp_path, dry_run=True)
    assert summary.covered == 1
    assert not covered_path(root).exists()


async def test_promote_accepts_the_member_rows_of_a_covered_candidate(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    hold(root, ALL[:10])
    candidates(root, ALL)
    summary = await verify(root, tmp_path)
    assert summary.member_rows == 2
    paths = proposal_paths(root, TODAY)
    report = run_promote(root, [paths.members], dry_run=True, industry=IndustryRoster())
    assert report.added == {THEME_KEY: 2}
    assert dict(report.rejects) == {}
    assert not report.written
