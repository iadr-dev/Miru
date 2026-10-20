"""Precision filters of the theme-candidate miner. Synthetic names and symbols only."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analytics.supply_chain import __main__ as cli
from analytics.supply_chain.domain.concepts import (
    ConceptMember,
    ConceptOrigin,
    ConceptPosition,
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    MentionSection,
)
from analytics.supply_chain.theme_discovery import io as tio
from analytics.supply_chain.theme_discovery.industry import load_industry_groups
from analytics.supply_chain.theme_discovery.mining import mine, specificity_score
from analytics.themes.models import (
    Theme,
    ThemeKind,
    ThemeMember,
    ThemeSource,
    ThemeStatus,
)

TODAY = date(2026, 10, 19)
_NOW = datetime(2026, 10, 19, tzinfo=UTC)


def ent(name: str, kind: EntityType = EntityType.TECHNOLOGY) -> Entity:
    return Entity(id=name, type=kind)


def firms(n: int) -> list[Entity]:
    return [ent(f"Firm {i:03d}", EntityType.TW_COMPANY) for i in range(n)]


def mentions(name: str, symbols: list[str]) -> list[EntityMention]:
    return [
        EntityMention(symbol=s, entity_id=name, section=MentionSection.BUSINESS) for s in symbols
    ]


def symbols(first: int, n: int) -> list[str]:
    return [str(first + i) for i in range(n)]


def concept(key: str, members: list[str], zh: str = "題材") -> ConceptTheme:
    return ConceptTheme(
        key=key,
        name_zh=zh,
        members=tuple(
            ConceptMember(
                symbol=s,
                market="TW",
                position=ConceptPosition.RELATED,
                origin=ConceptOrigin.CURATED,
            )
            for s in members
        ),
    )


def test_generic_term_is_dropped_by_the_share_cap_and_audited() -> None:
    catalog = [*firms(100), ent("Broad Term"), ent("Narrow Term")]
    rows = mentions("Broad Term", symbols(9000, 5)) + mentions("Narrow Term", symbols(9100, 4))
    out = mine(catalog, rows, [], min_companies=4)
    assert [c.label for c in out.candidates] == ["Narrow Term"]  # 4% of 100 = 4 stays, 5 goes
    (skip,) = out.skipped
    assert (skip.label, skip.reason, skip.company_count) == ("Broad Term", "generic", 5)
    out = mine(catalog, rows, [], min_companies=4, max_company_share=0.05)
    assert {c.label for c in out.candidates} == {"Broad Term", "Narrow Term"}
    assert out.skipped == []


def test_specificity_formula_and_ordering_with_different_universes() -> None:
    assert specificity_score(10, 1000) == round(10 * math.log(100), 4)
    assert specificity_score(10, 0) == 10.0  # unknown universe: plain company count
    assert specificity_score(10, 5) == 0.0
    # equal counts, more rare in the bigger universe: higher score
    assert specificity_score(8, 400) > specificity_score(8, 100)
    catalog = [*firms(400), ent("Alpha Tech"), ent("Beta Tech"), ent("Gamma Tech")]
    rows = (
        mentions("Alpha Tech", symbols(9000, 8))
        + mentions("Beta Tech", symbols(9100, 8))
        + mentions("Gamma Tech", symbols(9200, 12))
    )
    cands = mine(catalog, rows, [], max_company_share=0.5).candidates
    # 12 * ln(400/12) = 42.1 beats 8 * ln(50) = 31.3; equal scores tie by label
    assert [c.label for c in cands] == ["Gamma Tech", "Alpha Tech", "Beta Tech"]
    assert cands[0].specificity == specificity_score(12, 400)
    assert cands[1].specificity == cands[2].specificity


def test_ties_break_on_total_mentions_then_label() -> None:
    catalog = [*firms(400), ent("Alpha Tech"), ent("Beta Tech")]
    heavy = [
        EntityMention(symbol=s, entity_id="Beta Tech", section=MentionSection.BUSINESS, count=3)
        for s in symbols(9100, 8)
    ]
    rows = mentions("Alpha Tech", symbols(9000, 8)) + heavy
    assert [c.label for c in mine(catalog, rows, []).candidates] == ["Beta Tech", "Alpha Tech"]


@pytest.mark.parametrize(
    ("shared", "dropped"),
    [(6, True), (5, False)],  # 6/10 = 0.6 is covered ("at least"); 5/10 is not
)
def test_overlap_with_a_concept_theme_at_the_threshold(shared: int, dropped: bool) -> None:
    mine_symbols = symbols(9000, 10)
    theme = concept("owned-theme", [*mine_symbols[:shared], "9990", "9991"])
    out = mine([*firms(1000), ent("Widget Tech")], mentions("Widget Tech", mine_symbols), [theme])
    if dropped:
        assert out.candidates == []
        (skip,) = out.skipped
        assert skip.reason == "overlap:owned-theme"
    else:
        assert [c.label for c in out.candidates] == ["Widget Tech"]
        assert out.skipped == []


def test_overlap_counts_one_group_not_a_union() -> None:
    mine_symbols = symbols(9000, 10)
    themes = [concept("left", mine_symbols[:4]), concept("right", mine_symbols[4:8])]
    out = mine([*firms(1000), ent("Widget Tech")], mentions("Widget Tech", mine_symbols), themes)
    assert len(out.candidates) == 1  # 8 of 10 across two themes, but neither reaches 0.6


def test_overlap_with_an_injected_industry_group() -> None:
    mine_symbols = symbols(9000, 10)
    groups = {"industry:alpha": frozenset(mine_symbols[:7]), "sub:alpha/beta": frozenset(["1"])}
    rows = mentions("Widget Tech", mine_symbols)
    out = mine([*firms(1000), ent("Widget Tech")], rows, [], industry_groups=groups)
    assert out.candidates == []
    assert out.skipped[0].reason == "overlap:industry:alpha"
    assert len(mine([*firms(1000), ent("Widget Tech")], rows, []).candidates) == 1


def test_strongest_overlap_is_reported_and_threshold_is_configurable() -> None:
    mine_symbols = symbols(9000, 10)
    themes = [concept("b-theme", mine_symbols[:7]), concept("a-theme", mine_symbols[:9])]
    rows = mentions("Widget Tech", mine_symbols)
    catalog = [*firms(1000), ent("Widget Tech")]
    assert mine(catalog, rows, themes).skipped[0].reason == "overlap:a-theme"
    assert mine(catalog, rows, themes, overlap_threshold=0.95).skipped == []


def test_stoplist_matches_label_or_alias_with_name_normalisation() -> None:
    aliased = Entity(id="Gizmo Wave", type=EntityType.TECHNOLOGY, aliases=("Widget Tech",))
    catalog = [*firms(1000), aliased, ent("Other Thing")]
    rows = mentions("Gizmo Wave", symbols(9000, 6)) + mentions("Other Thing", symbols(9100, 6))
    out = mine(catalog, rows, [], stoplist=["WIDGET-TECH", " "])
    assert [c.label for c in out.candidates] == ["Other Thing"]
    assert [(s.label, s.reason) for s in out.skipped] == [("Gizmo Wave", "stoplist")]
    assert len(mine(catalog, rows, []).candidates) == 2


def test_covered_by_name_is_audited_too() -> None:
    out = mine(
        [*firms(1000), ent("Widget Tech")],
        mentions("Widget Tech", symbols(9000, 6)),
        [ConceptTheme(key="widget-tech", name_zh="題材")],
    )
    assert out.candidates == []
    assert [s.reason for s in out.skipped] == ["covered_by_name"]


def test_entities_below_the_company_threshold_are_not_audited() -> None:
    out = mine([*firms(1000), ent("Tiny Thing")], mentions("Tiny Thing", symbols(9000, 2)), [])
    assert out.candidates == [] and out.skipped == []


def test_no_company_catalog_means_no_share_cap() -> None:
    out = mine([ent("Widget Tech")], mentions("Widget Tech", symbols(9000, 30)), [])
    assert out.candidates[0].specificity == 30.0 and out.skipped == []


def test_mining_is_deterministic_for_any_input_order() -> None:
    catalog = [*firms(300), *(ent(f"Thing {i}") for i in range(6)), ent("Wide Thing")]
    rows = [m for i in range(6) for m in mentions(f"Thing {i}", symbols(9000 + 50 * i, 5 + i))]
    rows += mentions("Wide Thing", symbols(9500, 20))
    themes = [concept("t", symbols(9000, 4))]
    first = mine(catalog, rows, themes, today=TODAY)
    again = mine(list(reversed(catalog)), list(reversed(rows)), themes, today=TODAY)
    assert first == again
    assert first.skipped[0].reason in {"generic", "overlap:t"}


def _write_root(tmp_path: Path, *, stoplist: str | None = None) -> Path:
    root = tmp_path / "ref"
    (root / "entities").mkdir(parents=True)
    catalog = "".join(f"- {{id: Firm {i:03d}, type: tw_company}}\n" for i in range(200))
    catalog += "- {id: Broad Term, type: technology}\n- {id: Narrow Term, type: technology}\n"
    (root / "entities" / "catalog.yaml").write_text(catalog, encoding="utf-8")
    rows = [
        {"symbol": s, "entity": name, "section": "business"}
        for name, first, n in (("Broad Term", 9000, 30), ("Narrow Term", 9100, 6))
        for s in symbols(first, n)
    ]
    (root / "entities" / "mentions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    if stoplist is not None:
        (root / "themes").mkdir()
        (root / "themes" / "candidate_stoplist.yaml").write_text(stoplist, encoding="utf-8")
    return root


def test_skipped_file_is_written_and_candidates_carry_specificity(tmp_path: Path) -> None:
    root = _write_root(tmp_path)
    result = tio.run_theme_mining(root, today=TODAY)
    lines = (root / "themes" / "theme_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    (row,) = (json.loads(x) for x in lines)
    assert row["label"] == "Narrow Term"
    assert row["specificity"] == specificity_score(6, 200)
    skipped = [
        json.loads(x)
        for x in (root / "themes" / "theme_candidates_skipped.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [(s["label"], s["reason"], s["company_count"]) for s in skipped] == [
        ("Broad Term", "generic", 30)
    ]
    assert result.skipped[0].reason == "generic"
    assert not any("Firm" in line for line in lines)
    dry = _write_root(tmp_path / "dry")
    tio.run_theme_mining(dry, today=TODAY, dry_run=True)
    assert not (dry / "themes").exists()


def test_stoplist_file_is_read_and_a_missing_one_is_fine(tmp_path: Path) -> None:
    root = _write_root(tmp_path, stoplist="- narrow-term\n- Unrelated\n")
    assert tio.run_theme_mining(root, today=TODAY, dry_run=True).candidates == []
    plain = _write_root(tmp_path / "plain")
    assert len(tio.run_theme_mining(plain, today=TODAY, dry_run=True).candidates) == 1
    bad = _write_root(tmp_path / "bad", stoplist="{not: a list}\n")
    with pytest.raises(tio.ThemeMiningError, match="list"):
        tio.run_theme_mining(bad, today=TODAY, dry_run=True)


def test_cli_max_share_flag_and_no_company_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _write_root(tmp_path)
    monkeypatch.setattr("analytics.supply_chain.theme_discovery.job.reference_dir", lambda: root)
    assert cli.main(["discover", "--themes", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Narrow Term" in out and "Broad Term" not in out and "skipped=1" in out
    assert "Firm" not in out
    assert cli.main(["discover", "--themes", "--dry-run", "--max-share", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "Broad Term technology 30" in out and "skipped=0" in out


class _FakeThemeStore:
    def __init__(self) -> None:
        self._themes = [
            Theme(
                id="1",
                key="ind-a",
                name_zh="a",
                kind=ThemeKind.INDUSTRY,
                status=ThemeStatus.ACTIVE,
                source=ThemeSource.INDUSTRY_CHAIN,
                created_at=_NOW,
                updated_at=_NOW,
            )
        ]

    async def list_themes(
        self, *, kind: ThemeKind | None = None, status: ThemeStatus | None = None
    ) -> list[Theme]:
        return [t for t in self._themes if kind is None or t.kind is kind]

    async def get_theme_member_details(self, *, theme_key: str) -> list[ThemeMember]:
        def member(symbol: str, market: str, sub: str) -> ThemeMember:
            return ThemeMember(
                symbol=symbol, market=market, sub_industry=sub, source=ThemeSource.INDUSTRY_CHAIN
            )

        return [
            member("9001", "TW", "s1"),
            member("9002", "TW", "s1"),
            member("9003", "TW", ""),
            member("ZZZ", "US", "s1"),
        ]


async def test_industry_groups_come_from_the_theme_store() -> None:
    groups = await load_industry_groups(_FakeThemeStore())  # type: ignore[arg-type]
    assert groups == {
        "industry:ind-a": frozenset({"9001", "9002", "9003"}),
        "sub:ind-a/s1": frozenset({"9001", "9002"}),
    }
