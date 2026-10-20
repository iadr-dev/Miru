"""Concept-theme candidate mining (Step A). Synthetic names and symbols only."""

from __future__ import annotations

import json
from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest

from analytics.supply_chain import __main__ as cli
from analytics.supply_chain.domain.concepts import (
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    MentionSection,
)
from analytics.supply_chain.theme_discovery import io as tio
from analytics.supply_chain.theme_discovery.job import (
    SC_THEME_CANDIDATES_DATASET,
    run_theme_candidates_job,
)
from analytics.supply_chain.theme_discovery.mining import mine_candidates
from analytics.supply_chain.theme_discovery.models import PreviousCandidate, Trend
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

TODAY = date(2026, 10, 19)


def ent(
    name: str, kind: EntityType = EntityType.TECHNOLOGY, aliases: tuple[str, ...] = ()
) -> Entity:
    return Entity(id=name, type=kind, aliases=aliases)


def mentions(name: str, count: int, *, first: int = 9001, per: int = 1) -> list[EntityMention]:
    return [
        EntityMention(
            symbol=str(first + i), entity_id=name, section=MentionSection.BUSINESS, count=per
        )
        for i in range(count)
    ]


def theme(key: str, zh: str = "題材", en: str = "", related: tuple[str, ...] = ()) -> ConceptTheme:
    return ConceptTheme(key=key, name_zh=zh, name_en=en, related=related)


def test_threshold_counts_distinct_companies_not_rows() -> None:
    rows = [
        *mentions("Widget Tech", 4),
        EntityMention(
            symbol="9001", entity_id="Widget Tech", section=MentionSection.CHAIN, count=5
        ),
    ]
    assert mine_candidates([ent("Widget Tech")], rows, []) == []
    rows += mentions("Widget Tech", 5)
    (cand,) = mine_candidates([ent("Widget Tech")], rows, [])
    assert cand.company_count == 5
    assert cand.sections == ("business", "chain")
    assert mine_candidates([ent("Widget Tech")], rows, [], min_companies=6) == []


def test_company_entities_and_non_tw_symbols_are_not_candidates() -> None:
    company = ent("Gadget Corp", EntityType.TW_COMPANY)
    rows = [
        *mentions("Gadget Corp", 8),
        EntityMention(symbol="ABC", entity_id="Widget Tech", section=MentionSection.BUSINESS),
    ]
    assert mine_candidates([company, ent("Widget Tech")], rows, []) == []


@pytest.mark.parametrize(
    "existing",
    [
        theme("widget-tech"),
        theme("x", zh="Widget　Tech"),
        theme("x", en="WIDGET TECH"),
        theme("x", related=("widget tech",)),
        theme("x", zh="widget-tech"),
    ],
)
def test_covered_by_key_name_width_case_or_related(existing: ConceptTheme) -> None:
    assert mine_candidates([ent("Widget Tech")], mentions("Widget Tech", 6), [existing]) == []


def test_covered_when_an_alias_matches_a_theme() -> None:
    entity = ent("Widget Tech", aliases=("Gizmo Wave",))
    assert (
        mine_candidates([entity], mentions("Widget Tech", 6), [theme("k", en="Gizmo wave")]) == []
    )
    assert len(mine_candidates([entity], mentions("Widget Tech", 6), [theme("other")])) == 1


def test_near_duplicates_sharing_an_alias_merge_and_count_companies_once() -> None:
    a = ent("Widget Tech", aliases=("WT",))
    b = ent("Widget Technology", aliases=("wt",))
    rows = mentions("Widget Tech", 3) + mentions("Widget Technology", 4, first=9003)
    (cand,) = mine_candidates([a, b], rows, [])
    assert cand.company_count == 6  # 9003 is mentioned under both names, counted once
    assert cand.total_mentions == 7
    assert mine_candidates([b, a], rows, [])[0].candidate_id == cand.candidate_id


def test_noise_labels_are_ignored() -> None:
    names = ["A", "2024", chr(0xFF15), " "]
    catalog = [ent(n) for n in names if n.strip()]
    rows = [m for n in names if n.strip() for m in mentions(n, 9)]
    assert mine_candidates(catalog, rows, []) == []


def test_ranking_is_deterministic_and_ordered() -> None:
    catalog = [ent("Beta Tech"), ent("Alpha Tech"), ent("Gamma Tech"), ent("Delta Tech")]
    rows = (
        mentions("Beta Tech", 6)
        + mentions("Alpha Tech", 6)
        + mentions("Gamma Tech", 8)
        + mentions("Delta Tech", 6, per=3)
    )
    first = mine_candidates(catalog, rows, [], today=TODAY)
    assert [c.label for c in first] == ["Gamma Tech", "Delta Tech", "Alpha Tech", "Beta Tech"]
    assert first == mine_candidates(list(reversed(catalog)), list(reversed(rows)), [], today=TODAY)


def test_top_companies_ranked_by_mentions_then_symbol_and_capped() -> None:
    rows = mentions("Widget Tech", 20)
    rows.append(
        EntityMention(symbol="9040", entity_id="Widget Tech", section=MentionSection.CHAIN, count=9)
    )
    (cand,) = mine_candidates([ent("Widget Tech")], rows, [])
    assert len(cand.top_companies) == 15
    assert cand.top_companies[:3] == ("9040", "9001", "9002")


def test_cap() -> None:
    names = [f"Thing {i:02d}" for i in range(12)]
    rows = [m for n in names for m in mentions(n, 5)]
    assert len(mine_candidates([ent(n) for n in names], rows, [], limit=7)) == 7


def test_trend_against_previous_run() -> None:
    catalog = [ent(n) for n in ("Up Tech", "Same Tech", "Down Tech", "New Tech")]
    rows = [
        m
        for n, c in (("Up Tech", 9), ("Same Tech", 6), ("Down Tech", 5), ("New Tech", 7))
        for m in mentions(n, c)
    ]
    ids = {c.label: c.candidate_id for c in mine_candidates(catalog, rows, [], today=TODAY)}
    previous = {
        ids["Up Tech"]: PreviousCandidate(
            candidate_id=ids["Up Tech"], company_count=6, first_seen="2026-09-01"
        ),
        ids["Same Tech"]: PreviousCandidate(
            candidate_id=ids["Same Tech"], company_count=6, first_seen="2026-09-01"
        ),
        ids["Down Tech"]: PreviousCandidate(
            candidate_id=ids["Down Tech"], company_count=8, first_seen="2026-09-01"
        ),
    }
    got = {c.label: c for c in mine_candidates(catalog, rows, [], previous=previous, today=TODAY)}
    assert got["Up Tech"].trend is Trend.RISING
    assert got["Up Tech"].trend_text() == "rising (+3 companies)"
    assert got["Up Tech"].first_seen == "2026-09-01"
    assert got["Same Tech"].trend is Trend.STEADY
    assert got["Down Tech"].trend_text() == "falling (-3 companies)"
    assert got["New Tech"].trend is Trend.NEW
    assert got["New Tech"].first_seen == "2026-10-19"


def _companies(n: int = 200) -> str:
    return "".join(f"- {{id: Firm {i:03d}, type: tw_company}}\n" for i in range(n))


def _root(tmp_path: Path, *, n_companies: int = 6) -> Path:
    root = tmp_path / "ref"
    (root / "entities").mkdir(parents=True)
    (root / "entities" / "catalog.yaml").write_text(
        "- {id: Widget Tech, type: technology}\n" + _companies(), encoding="utf-8"
    )
    lines = [
        json.dumps({"symbol": str(9001 + i), "entity": "Widget Tech", "section": "business"})
        for i in range(n_companies)
    ]
    (root / "entities" / "mentions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_writes_atomically_rotates_previous_and_rerun_is_steady(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = tio.run_theme_mining(root, today=TODAY)
    assert first.written
    target = root / "themes" / "theme_candidates.jsonl"
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert [r["trend"] for r in rows] == ["new"]
    assert not list((root / "themes").glob(".*tmp*"))
    assert not (root / "themes" / "theme_candidates.prev.jsonl").exists()
    again = tio.run_theme_mining(root, today=date(2026, 10, 26))
    assert again.candidates[0].trend is Trend.STEADY
    assert again.candidates[0].first_seen == "2026-10-19"
    assert (root / "themes" / "theme_candidates.prev.jsonl").is_file()


def test_failed_write_leaves_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    tio.run_theme_mining(root, today=TODAY)
    target = root / "themes" / "theme_candidates.jsonl"
    before = target.read_text(encoding="utf-8")

    def boom(self: Path, target: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        tio.write_atomic(target, "garbage\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == before
    assert not list((root / "themes").glob(".*tmp*"))


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = tio.run_theme_mining(root, today=TODAY, dry_run=True)
    assert len(result.candidates) == 1
    assert not result.written
    assert not (root / "themes").exists()


def test_cli_prints_symbols_and_no_company_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    (root / "entities" / "catalog.yaml").write_text(
        "- {id: Widget Tech, type: technology}\n"
        "- {id: Gadget Corp, type: tw_company, symbol: '9001'}\n" + _companies(),
        encoding="utf-8",
    )
    monkeypatch.setattr("analytics.supply_chain.theme_discovery.job.reference_dir", lambda: root)
    assert cli.main(["discover", "--themes", "--dry-run", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "rank label type companies specificity trend symbols" in out
    # 6 * ln(201 / 6) = 21.07: the table carries the specificity next to the company count
    assert "Widget Tech technology 6 21.07 new 9001,9002,9003,9004,9005" in out
    assert "Gadget" not in out
    assert not (root / "themes").exists()
    assert cli.main(["discover", "--themes", "--min-companies", "7"]) == 0
    assert "candidates=0" in capsys.readouterr().out
    assert (root / "themes" / "theme_candidates.jsonl").read_text(encoding="utf-8") == ""


def test_cli_without_files_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "analytics.supply_chain.theme_discovery.job.reference_dir", lambda: tmp_path
    )
    assert cli.main(["discover", "--themes"]) == 1
    assert "cannot mine themes" in capsys.readouterr().err


def _ctx(tmp_path: Path, leader: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


async def test_job_skips_without_files_or_leadership_and_runs_otherwise(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    run = await run_theme_candidates_job(TODAY, _ctx(tmp_path, AlwaysLeader()), root=empty)
    assert run.status is RunStatus.SKIPPED
    assert "catalog" in run.detail
    root = _root(tmp_path)
    run = await run_theme_candidates_job(TODAY, _ctx(tmp_path, NeverLeader()), root=root)
    assert run.status is RunStatus.SKIPPED
    assert not (root / "themes").exists()
    run = await run_theme_candidates_job(TODAY, _ctx(tmp_path, AlwaysLeader()), root=root)
    assert run.status is RunStatus.SUCCESS
    assert run.dataset == SC_THEME_CANDIDATES_DATASET
    (root / "entities" / "mentions.jsonl").write_text("{oops\n", encoding="utf-8")
    run = await run_theme_candidates_job(TODAY, _ctx(tmp_path, AlwaysLeader()), root=root)
    assert run.status is RunStatus.FAILED


def test_job_is_scheduled_weekly_and_not_research_gated() -> None:
    jobs = {j.name: j for j in SCHEDULED_JOBS}
    assert jobs["sc_theme_candidates"].cadence.value == "weekly"
    assert [j.name for j in SCHEDULED_JOBS].count("sc_theme_candidates") == 1
