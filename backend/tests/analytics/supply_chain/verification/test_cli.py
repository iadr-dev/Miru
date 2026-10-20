from __future__ import annotations

import json
from pathlib import Path

import pytest

from analytics.supply_chain import __main__ as sc_main
from analytics.supply_chain.verification import cli as cli_module
from analytics.supply_chain.verification import job as job_module
from analytics.supply_chain.verification.directory import build_directory
from config.settings import Settings
from tests.analytics.supply_chain.verification.support import (
    PAGE_TEXT,
    SENTENCE,
    StubSearch,
    hit,
    judgement_json,
    llm,
)

URL = "https://news.example.com/p"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ref"
    (root / "supply_chain").mkdir(parents=True)
    (root / "supply_chain" / "publisher_trust.yaml").write_text(
        "news.example.com: secondary\n", encoding="utf-8"
    )
    line = {
        "supplier": "9001",
        "customer": "9002",
        "counterparty_name": "貝塔裝置",
        "counterparty_resolved": True,
        "kind": "supplies",
    }
    (root / "supply_chain" / "candidates.jsonl").write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )
    directory = build_directory(
        roster={"9001": "阿爾法零件", "9002": "貝塔裝置"}, market_values={"9001": 1.0}
    )
    settings = Settings(
        brave_search_api_key="k",  # type: ignore[arg-type]
        finmind_api_key="t",  # type: ignore[arg-type]
        parquet_root=str(tmp_path / "parquet"),
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "reference_dir", lambda: root)
    monkeypatch.setattr(job_module, "load_directory", lambda *_a, **_k: directory)
    monkeypatch.setattr(
        job_module, "resolve_role_with_override", lambda *_a, **_k: llm(judgement_json())
    )
    monkeypatch.setattr(
        job_module,
        "BraveSearchProvider",
        lambda **_k: StubSearch([hit(URL)], {URL: PAGE_TEXT}),
    )
    return root


async def test_discover_prints_claim_ids_only(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli_module.run_research_command(
        "discover", limit=5, dry_run=True, source="candidates"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "targets=1"
    assert "supplies:TW-9001>TW-9002 candidates" in out
    assert "阿爾法" not in out
    assert "貝塔" not in out


async def test_verify_dry_run_reports_counts_and_never_page_text(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli_module.run_research_command(
        "verify", limit=5, dry_run=True, source="candidates"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "claims=1 verified=1" in out
    assert "would_write=1" in out
    assert "supplies:TW-9001>TW-9002 verified" in out
    assert SENTENCE not in out
    assert PAGE_TEXT not in out
    assert "阿爾法" not in out
    assert not (env / "supply_chain" / "relations").exists()
    assert not (env / "supply_chain" / "rejected.jsonl").exists()


async def test_verify_without_a_search_key_fails_cleanly(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_key = Settings(brave_search_api_key=None, finmind_api_key="t")  # type: ignore[arg-type]
    monkeypatch.setattr(cli_module, "get_settings", lambda: no_key)
    code = await cli_module.run_research_command("verify", limit=1, dry_run=True, source="all")
    captured = capsys.readouterr()
    assert code == 1
    assert "no search key" in captured.err
    assert captured.out == ""


def test_argument_parser_accepts_the_new_commands() -> None:
    with pytest.raises(SystemExit):
        sc_main.main(["bogus"])
    with pytest.raises(SystemExit):
        sc_main.main(["verify", "--source", "nowhere"])
