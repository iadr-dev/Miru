"""Step A of concept-theme discovery: pure, deterministic mining of the entity-mention data.

A candidate is a technology / material / application / product entity that many DISTINCT Taiwan
companies mention and that no existing concept theme already covers. No LLM, no network, no clock
(the caller passes `today`). Near-duplicate entities that share a name or alias are merged first,
so one idea is counted once. Output order is a pure function of the input.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from analytics.supply_chain.domain.concepts import (
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    market_of_symbol,
)
from analytics.supply_chain.theme_discovery.models import PreviousCandidate, ThemeCandidate, Trend

DEFAULT_MIN_COMPANIES = 5
DEFAULT_LIMIT = 50
MAX_TOP_COMPANIES = 15
MIN_LABEL_LENGTH = 2
CANDIDATE_TYPES = frozenset(
    {EntityType.TECHNOLOGY, EntityType.MATERIAL, EntityType.APPLICATION, EntityType.PRODUCT}
)


def normalize(text: str) -> str:
    """Case, width and space/punctuation-insensitive form used for every name comparison."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def theme_names(theme: ConceptTheme) -> set[str]:
    """Every name a theme answers to: key, zh/en names and its related (anchor) names."""
    raw = [theme.key, theme.name_zh, theme.name_en, *theme.related]
    return {n for n in (normalize(r) for r in raw) if n}


def covered_names(themes: Iterable[ConceptTheme]) -> set[str]:
    names: set[str] = set()
    for theme in themes:
        names |= theme_names(theme)
    return names


def _names_of(entity: Entity) -> set[str]:
    return {n for n in (normalize(x) for x in (entity.id, *entity.aliases)) if n}


def is_noise(label: str) -> bool:
    norm = normalize(label)
    return len(label.strip()) < MIN_LABEL_LENGTH or len(norm) < MIN_LABEL_LENGTH or norm.isdigit()


class _Group:
    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.names: set[str] = set()


def _merge_groups(entities: Sequence[Entity]) -> list[_Group]:
    """Union entities that share a normalised name or alias (order-independent)."""
    parent = list(range(len(entities)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for index, entity in enumerate(entities):
        for name in _names_of(entity):
            if name in owner:
                low, high = sorted((find(owner[name]), find(index)))
                parent[high] = low
            else:
                owner[name] = index
    groups: dict[int, _Group] = defaultdict(_Group)
    for index, entity in enumerate(entities):
        group = groups[find(index)]
        group.entities.append(entity)
        group.names |= _names_of(entity)
    return list(groups.values())


def _candidate_id(group: _Group) -> str:
    seed = min(normalize(e.id) for e in group.entities)
    return "tc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _trend(count: int, previous: PreviousCandidate | None, today: str) -> tuple[str, Trend, int]:
    if previous is None:
        return today, Trend.NEW, 0
    first_seen = previous.first_seen or today
    delta = count - previous.company_count
    if delta > 0:
        return first_seen, Trend.RISING, delta
    if delta < 0:
        return first_seen, Trend.FALLING, delta
    return first_seen, Trend.STEADY, 0


def mine_candidates(  # noqa: PLR0913 - keyword-only knobs of one pure function
    catalog: Sequence[Entity],
    mentions: Sequence[EntityMention],
    existing_themes: Sequence[ConceptTheme],
    *,
    min_companies: int = DEFAULT_MIN_COMPANIES,
    limit: int = DEFAULT_LIMIT,
    previous: Mapping[str, PreviousCandidate] | None = None,
    today: date | None = None,
) -> list[ThemeCandidate]:
    covered = covered_names(existing_themes)
    pool = [e for e in catalog if e.type in CANDIDATE_TYPES and not is_noise(e.id)]
    by_entity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sections: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.count <= 0 or market_of_symbol(m.symbol) != "TW":
            continue
        by_entity[m.entity_id][m.symbol] += m.count
        sections[m.entity_id].add(m.section.value)
    stamp = (today or date.min).isoformat()
    prev = previous or {}
    found: list[ThemeCandidate] = []
    for group in _merge_groups(pool):
        if group.names & covered:
            continue
        per_company: dict[str, int] = defaultdict(int)
        sects: set[str] = set()
        for entity in group.entities:
            for symbol, count in by_entity.get(entity.id, {}).items():
                per_company[symbol] += count
            sects |= sections.get(entity.id, set())
        if len(per_company) < min_companies:
            continue
        rep = min(
            group.entities,
            key=lambda e: (
                -len(by_entity.get(e.id, {})),
                -sum(by_entity.get(e.id, {}).values()),
                e.id,
            ),
        )
        ranked = sorted(per_company.items(), key=lambda kv: (-kv[1], kv[0]))
        cid = _candidate_id(group)
        first_seen, trend, delta = _trend(len(per_company), prev.get(cid), stamp)
        found.append(
            ThemeCandidate(
                candidate_id=cid,
                entity_id=rep.id,
                label=rep.id,
                type=rep.type.value,
                company_count=len(per_company),
                total_mentions=sum(per_company.values()),
                top_companies=tuple(s for s, _ in ranked[:MAX_TOP_COMPANIES]),
                sections=tuple(sorted(sects)),
                first_seen=first_seen,
                trend=trend,
                trend_delta=delta,
            )
        )
    found.sort(key=lambda c: (-c.company_count, -c.total_mentions, c.label, c.candidate_id))
    return found[: max(limit, 0)]


__all__ = [
    "CANDIDATE_TYPES",
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_COMPANIES",
    "MAX_TOP_COMPANIES",
    "covered_names",
    "is_noise",
    "mine_candidates",
    "normalize",
    "theme_names",
]
