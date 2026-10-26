"""Characterization of `mine`: representative choice, aggregation, filter precedence, ordering and
the trend stamp. Synthetic names and symbols only."""

from __future__ import annotations

from datetime import date

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
from analytics.supply_chain.theme_discovery.mining import mine
from analytics.supply_chain.theme_discovery.models import PreviousCandidate, Trend

TODAY = date(2026, 10, 19)


def ent(
    name: str, kind: EntityType = EntityType.TECHNOLOGY, aliases: tuple[str, ...] = ()
) -> Entity:
    return Entity(id=name, type=kind, aliases=aliases)


def firms(n: int) -> list[Entity]:
    return [ent(f"Firm {i:03d}", EntityType.TW_COMPANY) for i in range(n)]


def rows(
    name: str,
    symbols: list[str],
    *,
    count: int = 1,
    section: MentionSection = MentionSection.BUSINESS,
) -> list[EntityMention]:
    return [EntityMention(symbol=s, entity_id=name, section=section, count=count) for s in symbols]


def syms(first: int, n: int) -> list[str]:
    return [str(first + i) for i in range(n)]


def theme(
    key: str, members: list[str], zh: str = "題材", related: tuple[str, ...] = ()
) -> ConceptTheme:
    return ConceptTheme(
        key=key,
        name_zh=zh,
        related=related,
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


def test_a_merged_group_is_represented_by_its_widest_then_heaviest_then_lowest_id_entity() -> None:
    catalog = [
        ent("Zeta Tech", aliases=("shared",)),
        ent("Alpha Tech", aliases=("shared",)),
        ent("Mid Tech", aliases=("shared",)),
    ]
    data = (
        rows("Zeta Tech", syms(9000, 4))
        + rows("Alpha Tech", syms(9000, 3))
        + rows("Mid Tech", syms(9010, 4), count=2)
    )
    (cand,) = mine(catalog, data, [], min_companies=3).candidates
    # Mid Tech and Zeta Tech tie on width 4; Mid Tech has more mentions
    assert cand.label == "Mid Tech"
    assert cand.entity_id == "Mid Tech"
    assert cand.company_count == 8
    assert cand.total_mentions == 4 + 3 + 8


def test_tie_on_width_and_mentions_picks_the_lowest_id() -> None:
    catalog = [ent("Zeta Tech", aliases=("shared",)), ent("Alpha Tech", aliases=("shared",))]
    data = rows("Zeta Tech", syms(9000, 4)) + rows("Alpha Tech", syms(9100, 4))
    (cand,) = mine(catalog, data, [], min_companies=3).candidates
    assert cand.label == "Alpha Tech"


def test_mentions_of_non_tw_symbols_zero_counts_and_company_entities_are_ignored() -> None:
    catalog = [ent("Widget Tech"), *firms(100)]
    data = (
        rows("Widget Tech", syms(9000, 3))
        + rows("Widget Tech", ["ZZZA", "ZZZB"])
        + rows("Widget Tech", syms(9100, 3), count=0)
        + rows("Firm 000", syms(9200, 6))
    )
    (cand,) = mine(catalog, data, [], min_companies=3).candidates
    assert (cand.company_count, cand.total_mentions) == (3, 3)
    assert mine(catalog, data, [], min_companies=4).candidates == []


def test_sections_are_collected_sorted_across_the_merged_entities() -> None:
    catalog = [ent("A Tech", aliases=("shared",)), ent("B Tech", aliases=("shared",))]
    data = rows("A Tech", syms(9000, 3), section=MentionSection.CHAIN) + rows(
        "B Tech", syms(9000, 3), section=MentionSection.BUSINESS
    )
    (cand,) = mine(catalog, data, [], min_companies=3).candidates
    assert cand.sections == ("business", "chain")
    assert cand.company_count == 3
    assert cand.total_mentions == 6


def test_filter_precedence_is_covered_then_stoplist_then_generic_then_overlap() -> None:
    members = syms(9000, 10)
    catalog = [*firms(10), ent("Widget Tech")]
    data = rows("Widget Tech", members)
    existing = [theme("other", members)]
    # all four would drop it: the first in the list wins
    covered = [theme("widget-tech", members)]
    out = mine(catalog, data, covered, stoplist=["Widget Tech"], min_companies=3)
    assert [s.reason for s in out.skipped] == ["covered_by_name"]
    out = mine(catalog, data, existing, stoplist=["widget tech"], min_companies=3)
    assert [s.reason for s in out.skipped] == ["stoplist"]
    out = mine(catalog, data, existing, min_companies=3)
    assert [s.reason for s in out.skipped] == ["generic"]
    out = mine([ent("Widget Tech")], data, existing, min_companies=3)  # no company universe
    assert [s.reason for s in out.skipped] == ["overlap:other"]
    assert out.candidates == []


def test_a_group_is_covered_when_any_merged_alias_is_a_theme_name() -> None:
    catalog = [ent("Widget Tech", aliases=("Gizmo",))]
    out = mine(catalog, rows("Widget Tech", syms(9000, 4)), [theme("gizmo", [])], min_companies=3)
    assert out.candidates == []
    assert [s.reason for s in out.skipped] == ["covered_by_name"]


def test_overlap_names_the_largest_share_and_ties_go_to_the_first_key() -> None:
    members = syms(9000, 10)
    data = rows("Widget Tech", members)
    catalog = [ent("Widget Tech")]
    themes = [theme("b-theme", members[:8]), theme("a-theme", members[:8]), theme("c", members[:7])]
    out = mine(catalog, data, themes, min_companies=3)
    assert [s.reason for s in out.skipped] == ["overlap:a-theme"]


def test_skipped_rows_carry_the_audit_fields_and_sort_by_reason_then_count_then_label() -> None:
    catalog = [ent("B Tech"), ent("A Tech"), ent("C Tech"), ent("D Tech")]
    data = (
        rows("B Tech", syms(9000, 4))
        + rows("A Tech", syms(9100, 4))
        + rows("C Tech", syms(9200, 6))
        + rows("D Tech", syms(9300, 3))
    )
    out = mine(catalog, data, [], stoplist=["a tech", "b tech", "c tech"], min_companies=3)
    assert [(s.reason, s.label, s.company_count) for s in out.skipped] == [
        ("stoplist", "C Tech", 6),
        ("stoplist", "A Tech", 4),
        ("stoplist", "B Tech", 4),
    ]
    first = out.skipped[0]
    assert first.type == "technology"
    assert first.candidate_id.startswith("tc-")
    assert first.specificity == 6.0
    assert [c.label for c in out.candidates] == ["D Tech"]


def test_limit_applies_after_ranking_and_non_positive_limits_return_nothing() -> None:
    catalog = [ent("A Tech"), ent("B Tech"), ent("C Tech")]
    data = (
        rows("A Tech", syms(9000, 3))
        + rows("B Tech", syms(9100, 5))
        + rows("C Tech", syms(9200, 4))
    )
    top = mine(catalog, data, [], min_companies=3, limit=2).candidates
    assert [c.label for c in top] == ["B Tech", "C Tech"]
    assert mine(catalog, data, [], min_companies=3, limit=0).candidates == []
    assert mine(catalog, data, [], min_companies=3, limit=-1).candidates == []


def test_top_companies_are_capped_and_the_stamp_defaults_without_a_date() -> None:
    catalog = [ent("A Tech")]
    (cand,) = mine(catalog, rows("A Tech", syms(9000, 20)), [], min_companies=3).candidates
    assert len(cand.top_companies) == 15
    assert cand.first_seen == date.min.isoformat()
    assert (cand.trend, cand.trend_delta) == (Trend.NEW, 0)


def test_previous_without_first_seen_falls_back_to_today() -> None:
    catalog = [ent("A Tech")]
    data = rows("A Tech", syms(9000, 5))
    cid = mine(catalog, data, [], min_companies=3).candidates[0].candidate_id
    prev = {cid: PreviousCandidate(candidate_id=cid, company_count=3, first_seen="")}
    (cand,) = mine(catalog, data, [], min_companies=3, previous=prev, today=TODAY).candidates
    assert (cand.trend, cand.trend_delta, cand.first_seen) == (Trend.RISING, 2, "2026-10-19")


def test_companies_below_the_threshold_are_neither_candidates_nor_audited() -> None:
    out = mine([ent("A Tech")], rows("A Tech", syms(9000, 2)), [], min_companies=3)
    assert (out.candidates, out.skipped) == ([], [])
