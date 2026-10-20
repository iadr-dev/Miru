"""The `research-themes` / `verify-themes` commands and the weekly `sc_theme_research` job."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from analytics.supply_chain import __main__ as cli
from analytics.supply_chain.theme_research import job as job_module
from analytics.supply_chain.theme_research.files import claims_path, read_claims, state_path
from analytics.supply_chain.theme_research.job import (
    SC_THEME_RESEARCH_DATASET,
    job_sc_theme_research,
    run_theme_command,
    run_theme_research_job,
)
from analytics.supply_chain.theme_research.proposals import proposal_paths
from analytics.supply_chain.verification import job as verification_job
from config.settings import Settings
from ingest.daemon import SCHEDULED_JOBS, Cadence
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from llm.registry import ProviderNotConfiguredError
from tests.analytics.supply_chain.theme_research.support import (
    CANDIDATE_LABEL,
    COMPANIES,
    THEME_KEY,
    THEME_ZH,
    TODAY,
    directory,
    make_ctx,
    make_root,
    settings,
)
from tests.analytics.supply_chain.verification.support import StubSearch, hit, llm

URL = "https://news.example.com/theme"
ZETA = COMPANIES["9101"][0]
ETA = COMPANIES["9102"][0]
PAGE = (
    f"產業新聞 2026-09-01\n\n{ZETA}是{THEME_ZH}的重要供應商。\n"
    f"{ETA}也投入{CANDIDATE_LABEL}的量產。\n其他無關內容。"
)


def reply(company: str, excerpt: str) -> str:
    row = {"company": company, "symbol": None, "excerpt": excerpt, "position": "upstream"}
    return json.dumps([row], ensure_ascii=False)


def replies() -> tuple[str, str]:
    """The candidate target is researched first, then the existing theme."""
    return (
        reply(ETA, f"{ETA}也投入{CANDIDATE_LABEL}的量產。"),
        reply(ZETA, f"{ZETA}是{THEME_ZH}的重要供應商。"),
    )


def concept_snapshot(root: Path) -> dict[str, str]:
    base = root / "themes" / "concepts"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(base.glob("*"))}


async def run_job(
    tmp_path: Path, root: Path, *, leader: object = None, cfg: Settings | None = None
) -> object:
    return await run_theme_research_job(
        TODAY,
        make_ctx(tmp_path, leader or AlwaysLeader()),
        settings=cfg or settings(),
        search=StubSearch([hit(URL)], {URL: PAGE}),
        client=llm(*replies()),
        directory=directory(),
        root=root,
    )


async def test_the_job_writes_claims_and_proposals_but_never_touches_live_themes(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    before = concept_snapshot(root)
    run = await run_job(tmp_path, root)
    assert run.status is RunStatus.SUCCESS and run.dataset == SC_THEME_RESEARCH_DATASET  # type: ignore[attr-defined]
    assert "research: targets=2 claims=2" in run.detail  # type: ignore[attr-defined]
    assert "verify: groups=2 verified=2" in run.detail  # type: ignore[attr-defined]
    assert run.rows_written == 3  # type: ignore[attr-defined]
    assert len(read_claims(claims_path(root))) == 2
    [row] = yaml.safe_load(proposal_paths(root, TODAY).members.read_text(encoding="utf-8"))
    assert row["theme_key"] == THEME_KEY and row["position"] == "upstream"
    assert not proposal_paths(root, TODAY).new_themes.exists()
    assert concept_snapshot(root) == before
    assert not (root / "themes" / "promote_log.jsonl").exists()
    assert not (root / "themes" / "_backups").exists()
    assert ZETA not in run.detail and PAGE not in run.detail  # type: ignore[attr-defined]


async def test_flag_off_is_skipped_without_any_work(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run = await run_job(tmp_path, root, cfg=settings(supply_chain_research_enabled=False))
    assert run.status is RunStatus.SKIPPED and "ENABLED" in run.detail  # type: ignore[attr-defined]
    assert not claims_path(root).exists() and not state_path(root).exists()


async def test_not_the_leader_is_skipped_without_any_work(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run = await run_job(tmp_path, root, leader=NeverLeader())
    assert run.status is RunStatus.SKIPPED and "leader" in run.detail  # type: ignore[attr-defined]
    assert not claims_path(root).exists()


async def test_missing_search_key_or_llm_role_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    ctx = make_ctx(tmp_path, AlwaysLeader())
    run = await run_theme_research_job(
        TODAY, ctx, settings=settings(brave_search_api_key=None), root=root
    )
    assert run.status is RunStatus.SKIPPED and "search key" in run.detail

    def no_provider(*_a: object, **_k: object) -> object:
        raise ProviderNotConfiguredError("nope")

    monkeypatch.setattr(verification_job, "resolve_role_with_override", no_provider)
    run = await run_theme_research_job(TODAY, ctx, settings=settings(), root=root)
    assert run.status is RunStatus.SKIPPED and "LLM" in run.detail


async def test_the_scheduled_entry_point_is_gated_by_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        job_module, "get_settings", lambda: settings(supply_chain_research_enabled=False)
    )
    run = await job_sc_theme_research(TODAY, make_ctx(tmp_path, AlwaysLeader()))
    assert run.dataset == SC_THEME_RESEARCH_DATASET and run.status is RunStatus.SKIPPED


async def test_an_invalid_theme_file_fails_the_run_without_writing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "themes" / "concepts" / "bad.yaml").write_text("- not a mapping\n", encoding="utf-8")
    run = await run_job(tmp_path, root)
    assert run.status is RunStatus.FAILED  # type: ignore[attr-defined]
    assert not claims_path(root).exists()


def test_job_is_scheduled_weekly_once_next_to_the_other_supply_chain_jobs() -> None:
    names = [j.name for j in SCHEDULED_JOBS]
    assert names.count("sc_theme_research") == 1
    jobs = {j.name: j for j in SCHEDULED_JOBS}
    assert jobs["sc_theme_research"].cadence is Cadence.WEEKLY
    assert jobs["sc_theme_research"].fire_time > jobs["sc_research"].fire_time
    assert "sc_theme_candidates" in names


@pytest.mark.parametrize("command", ["research-themes", "verify-themes"])
async def test_commands_refuse_without_a_search_key(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(job_module, "get_settings", lambda: settings(brave_search_api_key=None))
    code = await run_theme_command(command, limit=None, dry_run=False, source="all")
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err == f"cannot run {command}: no search key configured\n"


@pytest.mark.parametrize("command", ["research-themes", "verify-themes"])
async def test_commands_refuse_without_an_llm_role(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_provider(*_a: object, **_k: object) -> object:
        raise ProviderNotConfiguredError("nope")

    monkeypatch.setattr(job_module, "get_settings", settings)
    monkeypatch.setattr(verification_job, "resolve_role_with_override", no_provider)
    code = await run_theme_command(command, limit=None, dry_run=True, source="all")
    captured = capsys.readouterr()
    assert code == 1 and "no LLM role configured" in captured.err and captured.out == ""


async def test_verify_themes_needs_the_second_model_only_when_the_opinion_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def resolve(role: object, *_a: object, **_k: object) -> object:
        seen.append(str(role))
        return object()

    monkeypatch.setattr(verification_job, "resolve_role_with_override", resolve)
    assert job_module.command_blocker(settings(), "verify-themes") is None
    assert len(seen) == 1
    seen.clear()
    assert (
        job_module.command_blocker(settings(supply_chain_second_opinion=True), "verify-themes")
        is None
    )
    assert len(seen) == 2


def test_cli_dispatches_the_new_commands_and_validates_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int | None, bool, str]] = []

    async def fake(command: str, *, limit: int | None, dry_run: bool, source: str) -> int:
        calls.append((command, limit, dry_run, source))
        return 0

    monkeypatch.setattr(cli, "run_theme_command", fake)
    assert cli.main(["research-themes", "--limit", "3", "--source", "existing", "--dry-run"]) == 0
    assert cli.main(["verify-themes", "--limit", "2"]) == 0
    assert calls == [
        ("research-themes", 3, True, "existing"),
        ("verify-themes", 2, False, "all"),
    ]
    with pytest.raises(SystemExit):
        cli.main(["research-themes", "--source", "gaps"])
    with pytest.raises(SystemExit):
        cli.main(["verify", "--source", "existing"])


PACKAGE = (
    Path(__file__).resolve().parents[4] / "src" / "analytics" / "supply_chain" / "theme_research"
)
FORBIDDEN_ROOTS = {"sources", "httpx", "requests", "aiohttp", "socket", "urllib3", "websockets"}


def test_the_package_has_no_network_import_and_never_calls_promote() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 7
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert not roots & FORBIDDEN_ROOTS, path.name
        assert "dataaccess" not in roots, path.name
        assert "run_promote" not in path.read_text(encoding="utf-8"), path.name
