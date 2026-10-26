"""`python -m analytics.supply_chain` command dispatch, argument validation and exit codes, with
every handler stubbed so only the routing in `main` is exercised."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from analytics.supply_chain import __main__ as cli


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    seen: list[tuple[str, Any]] = []

    async def theme(command: str, **kw: Any) -> int:
        seen.append(("theme", (command, kw)))
        return 11

    async def research(command: str, **kw: Any) -> int:
        seen.append(("research", (command, kw)))
        return 12

    async def run(command: str, golden: Path | None, *, as_json: bool) -> int:
        seen.append(("run", (command, golden, as_json)))
        return 13

    def themes(**kw: Any) -> int:
        seen.append(("themes", kw))
        return 14

    def promote(files: list[Path], *, dry_run: bool, min_confidence: str) -> int:
        seen.append(("promote", (files, dry_run, min_confidence)))
        return 15

    monkeypatch.setattr(cli, "run_theme_command", theme)
    monkeypatch.setattr(cli, "run_research_command", research)
    monkeypatch.setattr(cli, "_run", run)
    monkeypatch.setattr(cli, "run_themes_command", themes)
    monkeypatch.setattr(cli, "_promote", promote)
    monkeypatch.setattr(cli, "_fetch_industry_groups", lambda: {"g": ("1",)})
    return seen


@pytest.mark.parametrize("command", ["load", "stats", "positions", "eval"])
def test_database_commands_go_to_run(command: str, calls: list[tuple[str, Any]]) -> None:
    assert cli.main([command, "--json", "--golden", "g.yaml"]) == 13
    assert calls == [("run", (command, Path("g.yaml"), True))]


def test_run_defaults(calls: list[tuple[str, Any]]) -> None:
    assert cli.main(["stats"]) == 13
    assert calls == [("run", ("stats", None, False))]


@pytest.mark.parametrize("command", ["discover", "verify"])
def test_discover_and_verify_go_to_the_research_command(
    command: str, calls: list[tuple[str, Any]]
) -> None:
    assert cli.main([command, "--limit", "4", "--dry-run", "--source", "gaps"]) == 12
    assert calls == [("research", (command, {"limit": 4, "dry_run": True, "source": "gaps"}))]


@pytest.mark.parametrize("command", ["research-themes", "verify-themes"])
def test_theme_commands_forward_every_option(command: str, calls: list[tuple[str, Any]]) -> None:
    code = cli.main([command, "--limit", "3", "--dry-run", "--covered-threshold", "0.5"])
    assert code == 11
    assert calls == [
        (
            "theme",
            (
                command,
                {"limit": 3, "dry_run": True, "source": "all", "covered_threshold": 0.5},
            ),
        )
    ]


def test_covered_threshold_bounds(
    calls: list[tuple[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["verify-themes", "--covered-threshold", "1"]) == 11
    for bad in ("0", "1.01", "-0.2"):
        with pytest.raises(SystemExit) as exc:
            cli.main(["verify-themes", "--covered-threshold", bad])
        assert exc.value.code == 2
    assert "--covered-threshold must be above 0 and at most 1" in capsys.readouterr().err
    assert len(calls) == 1


@pytest.mark.parametrize("source", ["gaps", "stale"])
def test_research_themes_rejects_claim_only_sources(
    source: str, calls: list[tuple[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["research-themes", "--source", source])
    assert exc.value.code == 2
    assert "research-themes --source is one of candidates|existing|all" in capsys.readouterr().err
    assert calls == []
    assert cli.main(["verify-themes", "--source", source]) == 11  # only research-themes rejects


@pytest.mark.parametrize("command", ["load", "stats", "positions", "eval", "discover", "verify"])
def test_source_existing_is_for_research_themes_only(
    command: str, calls: list[tuple[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--source", "existing"])
    assert exc.value.code == 2
    assert "--source existing applies to research-themes only" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("command", ["load", "stats", "positions", "eval", "verify"])
def test_themes_flag_is_for_discover_only(
    command: str, calls: list[tuple[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--themes"])
    assert exc.value.code == 2
    assert "--themes applies to the discover command only" in capsys.readouterr().err
    assert calls == []


def test_discover_themes_forwards_options_and_the_industry_groups(
    calls: list[tuple[str, Any]],
) -> None:
    code = cli.main(
        [
            "discover",
            "--themes",
            "--min-companies",
            "7",
            "--limit",
            "5",
            "--dry-run",
            "--max-share",
            "0.1",
            "--overlap",
            "0.9",
        ]
    )
    assert code == 14
    assert calls == [
        (
            "themes",
            {
                "industry_groups": {"g": ("1",)},
                "min_companies": 7,
                "limit": 5,
                "dry_run": True,
                "max_share": 0.1,
                "overlap_threshold": 0.9,
            },
        )
    ]


def test_discover_themes_says_so_when_the_database_is_unavailable(
    calls: list[tuple[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_fetch_industry_groups", lambda: None)
    assert cli.main(["discover", "--themes"]) == 14
    assert capsys.readouterr().out == "industry overlap skipped: database unavailable\n"
    assert calls[0][1]["industry_groups"] is None
    assert calls[0][1]["min_companies"] is None


def test_promote_gets_its_files_and_options(calls: list[tuple[str, Any]]) -> None:
    code = cli.main(["promote", "a.yaml", "b.yaml", "--dry-run", "--min-confidence", "high"])
    assert code == 15
    assert calls == [("promote", ([Path("a.yaml"), Path("b.yaml")], True, "high"))]
    calls.clear()
    assert cli.main(["promote", "a.yaml"]) == 15
    assert calls == [("promote", ([Path("a.yaml")], False, "medium"))]


@pytest.mark.parametrize("command", ["load", "discover", "verify", "research-themes"])
def test_proposal_files_apply_to_promote_only(
    command: str, calls: list[tuple[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "x.yaml"])
    assert exc.value.code == 2
    assert "proposal files apply to the promote command only" in capsys.readouterr().err
    assert calls == []


def test_unknown_command_is_an_argparse_error(calls: list[tuple[str, Any]]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["bogus"])
    assert exc.value.code == 2
    assert calls == []
