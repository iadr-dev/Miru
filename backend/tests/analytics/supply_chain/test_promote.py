"""`promote`: reviewed proposal files into live concept-theme files. Synthetic data only."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from analytics.supply_chain import __main__ as cli
from analytics.supply_chain.theme_discovery import job as theme_job
from analytics.supply_chain.themes_io import promote as pr
from analytics.supply_chain.themes_io.promote import (
    IndustryRoster,
    PromoteWriteError,
    ProposalFileError,
    run_promote,
)

CATALOG = [
    {"id": "Alpha Works", "type": "tw_company", "symbol": "2001", "aliases": ["Alfa Wks"]},
    {"id": "Beta Corp", "type": "tw_company", "symbol": "2002"},
    {"id": "Gamma Ltd", "type": "tw_company", "symbol": "2003"},
    {"id": "Delta Inc", "type": "tw_company", "symbol": "2004"},
]
INDUSTRY = IndustryRoster(
    keys=frozenset({"ind-key"}), names=frozenset({"工業題材"}), symbols=frozenset({"2005"})
)


def member(symbol: str = "2002", name: str = "Beta Corp", **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "name": name,
        "position": "midstream",
        "publisher": "Example News",
        "url": "https://example.test/a",
        "excerpt": f"{name} ships widgets to the demo market.",
        "confidence": "high",
    }
    return row | over


def addition(**over: Any) -> dict[str, Any]:
    return {"theme_key": "demo-theme", **member(), **over}


def new_theme(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "fresh-theme",
        "name_zh": "新題材",
        "name_en": "Fresh Theme",
        "description": "d",
        "related": ["Other"],
        "thin": False,
        "members": [member("2003", "Gamma Ltd")],
    }
    return row | over


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "catalog.yaml").write_text(yaml.safe_dump(CATALOG), encoding="utf-8")
    concepts = tmp_path / "themes" / "concepts"
    concepts.mkdir(parents=True)
    existing = {
        "key": "demo-theme",
        "name_zh": "示範題材",
        "name_en": "Demo Theme",
        "description": "",
        "related": [],
        "members": [{"symbol": "2001", "name": "Alpha Works", "position": "upstream",
                     "origin": "seeded"}],
    }  # fmt: skip
    (concepts / "demo-theme.yaml").write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tmp_path


def proposal(tmp_path: Path, rows: list[dict[str, Any]], name: str = "p.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(rows, allow_unicode=True), encoding="utf-8")
    return path


def promote(root: Path, rows: list[dict[str, Any]], **kw: Any) -> pr.PromoteReport:
    kw.setdefault("industry", INDUSTRY)
    return run_promote(root, [proposal(root, rows)], **kw)


def concept(root: Path, key: str = "demo-theme") -> dict[str, Any]:
    path = root / "themes" / "concepts" / f"{key}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_member_addition_is_written_as_proposed_preserving_order(root: Path) -> None:
    report = promote(root, [addition()])
    assert report.added == {"demo-theme": 1}
    data = concept(root)
    assert list(data) == ["key", "name_zh", "name_en", "description", "related", "members"]
    assert data["members"][1] == {
        "symbol": "2002",
        "name": "Beta Corp",
        "position": "midstream",
        "origin": "proposed",
    }
    assert "示範題材" in (root / "themes" / "concepts" / "demo-theme.yaml").read_text("utf-8")


def test_new_theme_row_creates_a_file(root: Path) -> None:
    report = promote(root, [new_theme()])
    assert report.new_themes == ["fresh-theme"]
    data = concept(root, "fresh-theme")
    assert data["name_zh"] == "新題材"
    assert "thin" not in data
    assert data["members"][0]["origin"] == "proposed"


def test_both_shapes_in_one_file_and_member_for_new_theme(root: Path) -> None:
    rows = [new_theme(), addition(theme_key="fresh-theme", symbol="2004", name="Delta Inc",
                                  excerpt="Delta Inc is named."), addition()]  # fmt: skip
    report = promote(root, rows)
    assert report.added == {"fresh-theme": 2, "demo-theme": 1}
    assert len(concept(root, "fresh-theme")["members"]) == 2


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (addition(theme_key="nope"), pr.UNKNOWN_THEME),
        (addition(symbol="9999"), pr.UNKNOWN_SYMBOL),
        (addition(position="sideways"), pr.INVALID_POSITION),
        (addition(url="http://example.test/a"), pr.EVIDENCE_MISSING),
        (addition(url=""), pr.EVIDENCE_MISSING),
        (addition(excerpt=""), pr.EVIDENCE_MISSING),
        (addition(excerpt="Beta Corp " + "x" * 200), pr.EVIDENCE_MISSING),
        (addition(excerpt="Nobody is named here."), pr.EVIDENCE_MISSING),
        (addition(confidence="low"), pr.LOW_CONFIDENCE),
        (
            addition(symbol="2001", name="Alpha Works", excerpt="Alpha Works is named."),
            pr.ALREADY_MEMBER,
        ),
        ({"nonsense": 1}, pr.INVALID_ROW),
        (new_theme(key="demo-theme"), pr.THEME_EXISTS),
        (new_theme(name_zh="示範題材"), pr.THEME_EXISTS),
        (new_theme(name_zh="  DEMO theme ", name_en=""), pr.THEME_EXISTS),
        (new_theme(key="ind-key"), pr.INDUSTRY_COLLISION),
        (new_theme(name_zh="工業題材"), pr.INDUSTRY_COLLISION),
        (new_theme(name_zh=""), pr.SCHEMA_INVALID),
        (new_theme(members=[]), pr.NO_VALID_MEMBERS),
        (new_theme(members=[member("2003", "Gamma Ltd", confidence="low")]), pr.LOW_CONFIDENCE),
    ],
)
def test_each_reject_reason_is_counted_and_nothing_written(
    root: Path, row: dict[str, Any], reason: str
) -> None:
    before = (root / "themes" / "concepts" / "demo-theme.yaml").read_text("utf-8")
    report = promote(root, [row])
    assert report.rejects[reason] >= 1
    assert not report.added
    assert (root / "themes" / "concepts" / "demo-theme.yaml").read_text("utf-8") == before
    assert not (root / "themes" / "_backups").exists()
    assert not report.written


def test_duplicate_symbol_within_a_new_theme(root: Path) -> None:
    dup = [member("2003", "Gamma Ltd"), member("2003", "Gamma Ltd")]
    report = promote(root, [new_theme(members=dup)])
    assert report.rejects[pr.DUPLICATE_SYMBOL] == 1
    assert report.added == {"fresh-theme": 1}


def test_alias_and_width_case_insensitive_excerpt_match(root: Path) -> None:
    row = addition(symbol="2001", name="", excerpt="ＡＬＦＡ　ＷＫＳ expands capacity.")  # noqa: RUF001
    row["theme_key"] = "demo-theme"
    report = promote(root, [row])
    # already a member, but the evidence check (alias, fullwidth) passed first
    assert report.rejects == {pr.ALREADY_MEMBER: 1}
    fresh = new_theme(members=[member("2001", "", excerpt="alfa wks is mentioned")])
    assert promote(root, [fresh]).new_themes == ["fresh-theme"]


def test_min_confidence_high_rejects_medium(root: Path) -> None:
    rows = [addition(confidence="medium")]
    assert promote(root, rows, dry_run=True).added == {"demo-theme": 1}
    report = promote(root, rows, dry_run=True, min_confidence="high")
    assert report.rejects == {pr.LOW_CONFIDENCE: 1}


def test_idempotent(root: Path) -> None:
    rows = [addition(), new_theme()]
    promote(root, rows)
    snapshot = {p.name: p.read_text("utf-8") for p in (root / "themes" / "concepts").iterdir()}
    again = promote(root, rows)
    assert not again.added
    assert again.rejects == {pr.ALREADY_MEMBER: 1, pr.THEME_EXISTS: 1}
    assert snapshot == {
        p.name: p.read_text("utf-8") for p in (root / "themes" / "concepts").iterdir()
    }


def test_dry_run_writes_nothing(root: Path) -> None:
    report = promote(root, [addition(), new_theme()], dry_run=True)
    assert report.added == {"demo-theme": 1, "fresh-theme": 1}
    assert not (root / "themes" / "_backups").exists()
    assert not (root / "themes" / "promote_log.jsonl").exists()
    assert not (root / "themes" / "concepts" / "fresh-theme.yaml").exists()
    assert len(concept(root)["members"]) == 1


def test_backup_taken_and_rotation_keeps_five(root: Path) -> None:
    base = root / "themes" / "_backups"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(6):
        stamp = (start + timedelta(days=i)).strftime("%Y%m%dT%H%M%S%fZ")
        (base / f"concepts-{stamp}").mkdir(parents=True)
    now = datetime(2026, 6, 1, tzinfo=UTC)
    promote(root, [addition()], now=lambda: now)
    kept = sorted(p.name for p in base.iterdir())
    assert len(kept) == 5
    assert kept[-1] == "concepts-20260601T000000000000Z"
    assert "concepts-20260101T000000000000Z" not in kept
    backup = yaml.safe_load((base / kept[-1] / "demo-theme.yaml").read_text("utf-8"))
    assert len(backup["members"]) == 1  # the pre-change copy


def test_all_or_nothing_when_a_resulting_file_is_invalid(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = pr._validate_result
    seen: list[str] = []

    def picky(theme: pr._Theme) -> None:
        seen.append(theme.path.name)
        if theme.path.name == "fresh-theme.yaml":
            raise PromoteWriteError("bad")
        real(theme)

    monkeypatch.setattr(pr, "_validate_result", picky)
    before = (root / "themes" / "concepts" / "demo-theme.yaml").read_text("utf-8")
    with pytest.raises(PromoteWriteError):
        promote(root, [addition(), new_theme()])
    assert (root / "themes" / "concepts" / "demo-theme.yaml").read_text("utf-8") == before
    assert not (root / "themes" / "concepts" / "fresh-theme.yaml").exists()
    assert not (root / "themes" / "_backups").exists()
    assert not (root / "themes" / "promote_log.jsonl").exists()


def test_log_line_has_counts_and_no_names(root: Path) -> None:
    promote(root, [addition(), addition(symbol="9999", name="Zeta Secret Co"), new_theme()])
    (line,) = (root / "themes" / "promote_log.jsonl").read_text("utf-8").splitlines()
    entry = json.loads(line)
    assert entry["inputs"] == ["p.yaml"]
    assert entry["added_members"] == 2
    assert entry["new_themes"] == 1
    assert entry["rejects"] == {pr.UNKNOWN_SYMBOL: 1}
    for name in ("Beta Corp", "Gamma Ltd", "Zeta Secret Co", "Alpha"):
        assert name not in line


def test_industry_unavailable_warns_and_checks_concepts_only(root: Path) -> None:
    report = promote(root, [new_theme(key="ind-key"), addition(symbol="2005", name="Epsilon")],
                     industry=None, dry_run=True)  # fmt: skip
    assert report.warnings
    assert report.new_themes == ["ind-key"]  # not detectable without the roster
    assert report.rejects == {pr.UNKNOWN_SYMBOL: 1}  # 2005 only lives in the industry roster


def test_industry_roster_symbols_count_as_taiwan_roster(root: Path) -> None:
    row = addition(symbol="2005", name="Epsilon", excerpt="Epsilon is named.")
    assert promote(root, [row], dry_run=True).added == {"demo-theme": 1}


def test_unusable_proposal_file_raises(root: Path) -> None:
    bad = root / "bad.yaml"
    bad.write_text("key: not-a-list\n", encoding="utf-8")
    with pytest.raises(ProposalFileError):
        run_promote(root, [bad], industry=INDUSTRY)
    bad.write_text("- [unclosed\n", encoding="utf-8")
    with pytest.raises(ProposalFileError):
        run_promote(root, [bad], industry=INDUSTRY)


# --- CLI -------------------------------------------------------------------------------------


@pytest.fixture
def wired(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "reference_dir", lambda: root)
    monkeypatch.setattr(cli, "_fetch_industry_roster", lambda: INDUSTRY)
    return root


def test_cli_exit_codes_and_output(wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = proposal(wired, [addition(), addition(symbol="9999", name="Zeta Secret Co")])
    assert cli.main(["promote", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "added_members=1" in out
    assert "unknown_symbol=1 symbols=9999" in out
    assert "Beta" not in out
    assert "dry run: nothing written" in out
    assert "next:" not in out
    assert cli.main(["promote", str(path)]) == 0
    out = capsys.readouterr().out
    assert "theme demo-theme: +1 members" in out
    assert "analytics.supply_chain load" in out
    assert cli.main(["promote", str(wired / "missing.yaml")]) == 1
    assert "invalid file" in capsys.readouterr().err
    assert cli.main(["promote"]) == 1


def test_cli_warns_when_database_is_unavailable(
    wired: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_fetch_industry_roster", lambda: None)
    path = proposal(wired, [addition()])
    assert cli.main(["promote", str(path), "--dry-run", "--min-confidence", "high"]) == 0
    assert "warning: industry roster unavailable" in capsys.readouterr().out


def test_cli_files_only_for_promote() -> None:
    with pytest.raises(SystemExit):
        cli.main(["load", "x.yaml"])


# --- discover --themes industry overlap ------------------------------------------------------


def _themes_root(tmp_path: Path) -> Path:
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "catalog.yaml").write_text("[]\n", encoding="utf-8")
    (tmp_path / "entities" / "mentions.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_discover_themes_notes_when_database_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _themes_root(tmp_path)
    monkeypatch.setattr(theme_job, "reference_dir", lambda: root)
    monkeypatch.setattr(cli, "_fetch_industry_groups", lambda: None)
    assert cli.main(["discover", "--themes", "--dry-run"]) == 0
    assert "industry overlap skipped: database unavailable" in capsys.readouterr().out


def test_discover_themes_passes_industry_groups_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _themes_root(tmp_path)
    monkeypatch.setattr(theme_job, "reference_dir", lambda: root)
    groups = {"industry:x": frozenset({"2001"})}
    monkeypatch.setattr(cli, "_fetch_industry_groups", lambda: groups)
    captured: dict[str, Any] = {}
    real = theme_job.run_theme_mining

    def spy(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(theme_job, "run_theme_mining", spy)
    assert cli.main(["discover", "--themes", "--dry-run"]) == 0
    assert captured["industry_groups"] == groups
    assert "industry overlap skipped" not in capsys.readouterr().out


def test_database_failure_never_fails_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> object:
        raise OSError("no db")

    monkeypatch.setattr(cli, "get_settings", boom)
    assert cli._fetch_industry_groups() is None
    assert cli._fetch_industry_roster() is None
