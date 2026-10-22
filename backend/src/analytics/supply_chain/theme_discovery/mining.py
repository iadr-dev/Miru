"""Step A of concept-theme discovery: pure, deterministic mining of the entity-mention data.

A candidate is a technology / material / application / product entity that many DISTINCT Taiwan
companies mention and that no existing concept theme already covers. No LLM, no network, no clock
(the caller passes `today`). Near-duplicate entities that share a name or alias are merged first,
so one idea is counted once. Output order is a pure function of the input.

Precision filters, applied in this order to every entity that reaches `min_companies`:
  1. `covered_by_name`: a concept theme already answers to the entity's name or alias.
  2. `stoplist`: the optional private file `<reference dir>/themes/candidate_stoplist.yaml`, a YAML
     list of entity labels or aliases (`- some label`), matched with the same normalisation as the
     name coverage above. A missing file means no stoplist.
  3. `generic`: more than `max_company_share` (default 0.04) of all `tw_company` entities in the
     catalog mention it. Without a company catalog the universe is unknown and this cap is off.
  4. `overlap:<key>`: at least `overlap_threshold` (default 0.6) of its companies already belong to
     ONE group: a concept theme (key = theme key) or an injected industry group (`industry:` theme
     or `sub:` sub-industry key). The same companies under a different name.
Survivors are ranked by specificity = company_count * ln(N_tw / company_count) (tf-idf style, so a
broad term loses to a focused one), then total mentions, then label. Dropped entities are returned
separately so the operator can audit why.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from analytics.supply_chain.domain.concepts import (
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    market_of_symbol,
)
from analytics.supply_chain.theme_discovery.models import (
    PreviousCandidate,
    SkippedCandidate,
    ThemeCandidate,
    Trend,
)

DEFAULT_MIN_COMPANIES = 5
DEFAULT_LIMIT = 50
DEFAULT_MAX_COMPANY_SHARE = 0.04
DEFAULT_OVERLAP_THRESHOLD = 0.6
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


@dataclass(frozen=True)
class MiningOutcome:
    candidates: list[ThemeCandidate]
    skipped: list[SkippedCandidate]


def specificity_score(company_count: int, n_tw: int) -> float:
    """company_count * ln(N / company_count); plain company_count when N is unknown (0)."""
    if n_tw <= 0 or company_count <= 0:
        return float(max(company_count, 0))
    return round(company_count * max(math.log(n_tw / company_count), 0.0), 4)


def concept_groups(themes: Iterable[ConceptTheme]) -> dict[str, frozenset[str]]:
    """TW member symbols of each concept theme, keyed by theme key."""
    out: dict[str, frozenset[str]] = {}
    for theme in themes:
        symbols = frozenset(m.symbol for m in theme.members if m.market == "TW")
        if symbols:
            out[theme.key] = symbols
    return out


def _best_overlap(
    companies: frozenset[str], groups: Mapping[str, frozenset[str]], threshold: float
) -> str | None:
    best: tuple[float, str] | None = None
    for key in sorted(groups):
        share = len(companies & groups[key]) / len(companies)
        if share >= threshold and (best is None or share > best[0]):
            best = (share, key)
    return best[1] if best else None


@dataclass(frozen=True)
class _Filters:
    """The precision filters of one run, applied in their documented order."""

    covered: set[str]
    stop: set[str]
    n_tw: int
    max_company_share: float
    groups: Mapping[str, frozenset[str]]
    overlap_threshold: float

    def reason(self, group: _Group, per_company: Mapping[str, int]) -> str | None:
        if group.names & self.covered:
            return "covered_by_name"
        if group.names & self.stop:
            return "stoplist"
        if self.n_tw > 0 and len(per_company) / self.n_tw > self.max_company_share:
            return "generic"
        hit = _best_overlap(frozenset(per_company), self.groups, self.overlap_threshold)
        return f"overlap:{hit}" if hit is not None else None


def _index_mentions(
    mentions: Sequence[EntityMention],
) -> tuple[dict[str, dict[str, int]], dict[str, set[str]]]:
    """Per entity: mentions per TW company, and the sections it was mentioned in."""
    by_entity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sections: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.count <= 0 or market_of_symbol(m.symbol) != "TW":
            continue
        by_entity[m.entity_id][m.symbol] += m.count
        sections[m.entity_id].add(m.section.value)
    return by_entity, sections


def _group_totals(
    group: _Group, by_entity: Mapping[str, dict[str, int]], sections: Mapping[str, set[str]]
) -> tuple[dict[str, int], set[str]]:
    per_company: dict[str, int] = defaultdict(int)
    sects: set[str] = set()
    for entity in group.entities:
        for symbol, count in by_entity.get(entity.id, {}).items():
            per_company[symbol] += count
        sects |= sections.get(entity.id, set())
    return per_company, sects


def _representative(group: _Group, by_entity: Mapping[str, dict[str, int]]) -> Entity:
    return min(
        group.entities,
        key=lambda e: (
            -len(by_entity.get(e.id, {})),
            -sum(by_entity.get(e.id, {}).values()),
            e.id,
        ),
    )


def _candidate(  # noqa: PLR0913 - one value per output field
    cid: str,
    rep: Entity,
    per_company: Mapping[str, int],
    *,
    sects: set[str],
    score: float,
    trend: tuple[str, Trend, int],
) -> ThemeCandidate:
    ranked = sorted(per_company.items(), key=lambda kv: (-kv[1], kv[0]))
    first_seen, trend_kind, delta = trend
    return ThemeCandidate(
        candidate_id=cid,
        entity_id=rep.id,
        label=rep.id,
        type=rep.type.value,
        company_count=len(per_company),
        total_mentions=sum(per_company.values()),
        specificity=score,
        top_companies=tuple(s for s, _ in ranked[:MAX_TOP_COMPANIES]),
        sections=tuple(sorted(sects)),
        first_seen=first_seen,
        trend=trend_kind,
        trend_delta=delta,
    )


def mine(  # noqa: PLR0913 - keyword-only knobs of one pure function
    catalog: Sequence[Entity],
    mentions: Sequence[EntityMention],
    existing_themes: Sequence[ConceptTheme],
    *,
    min_companies: int = DEFAULT_MIN_COMPANIES,
    limit: int = DEFAULT_LIMIT,
    previous: Mapping[str, PreviousCandidate] | None = None,
    today: date | None = None,
    max_company_share: float = DEFAULT_MAX_COMPANY_SHARE,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    stoplist: Collection[str] = (),
    industry_groups: Mapping[str, frozenset[str]] | None = None,
) -> MiningOutcome:
    n_tw = sum(1 for e in catalog if e.type is EntityType.TW_COMPANY)
    groups: dict[str, frozenset[str]] = dict(concept_groups(existing_themes))
    groups.update(industry_groups or {})
    filters = _Filters(
        covered=covered_names(existing_themes),
        stop={n for n in (normalize(s) for s in stoplist) if n},
        n_tw=n_tw,
        max_company_share=max_company_share,
        groups=groups,
        overlap_threshold=overlap_threshold,
    )
    pool = [e for e in catalog if e.type in CANDIDATE_TYPES and not is_noise(e.id)]
    by_entity, sections = _index_mentions(mentions)
    stamp = (today or date.min).isoformat()
    prev = previous or {}
    found: list[ThemeCandidate] = []
    skipped: list[SkippedCandidate] = []
    for group in _merge_groups(pool):
        per_company, sects = _group_totals(group, by_entity, sections)
        if len(per_company) < min_companies:
            continue
        rep = _representative(group, by_entity)
        cid = _candidate_id(group)
        n_companies = len(per_company)
        score = specificity_score(n_companies, n_tw)
        reason = filters.reason(group, per_company)
        if reason is not None:
            skipped.append(
                SkippedCandidate(
                    candidate_id=cid,
                    label=rep.id,
                    type=rep.type.value,
                    company_count=n_companies,
                    specificity=score,
                    reason=reason,
                )
            )
            continue
        trend = _trend(n_companies, prev.get(cid), stamp)
        found.append(_candidate(cid, rep, per_company, sects=sects, score=score, trend=trend))
    found.sort(key=lambda c: (-c.specificity, -c.total_mentions, c.label, c.candidate_id))
    skipped.sort(key=lambda s: (s.reason, -s.company_count, s.label, s.candidate_id))
    return MiningOutcome(found[: max(limit, 0)], skipped)


def mine_candidates(  # noqa: PLR0913 - mirrors `mine`
    catalog: Sequence[Entity],
    mentions: Sequence[EntityMention],
    existing_themes: Sequence[ConceptTheme],
    *,
    min_companies: int = DEFAULT_MIN_COMPANIES,
    limit: int = DEFAULT_LIMIT,
    previous: Mapping[str, PreviousCandidate] | None = None,
    today: date | None = None,
    max_company_share: float = DEFAULT_MAX_COMPANY_SHARE,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    stoplist: Collection[str] = (),
    industry_groups: Mapping[str, frozenset[str]] | None = None,
) -> list[ThemeCandidate]:
    """`mine(...).candidates`, for callers that do not need the skipped list."""
    return mine(
        catalog,
        mentions,
        existing_themes,
        min_companies=min_companies,
        limit=limit,
        previous=previous,
        today=today,
        max_company_share=max_company_share,
        overlap_threshold=overlap_threshold,
        stoplist=stoplist,
        industry_groups=industry_groups,
    ).candidates


__all__ = [
    "CANDIDATE_TYPES",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_COMPANY_SHARE",
    "DEFAULT_MIN_COMPANIES",
    "DEFAULT_OVERLAP_THRESHOLD",
    "MAX_TOP_COMPANIES",
    "MiningOutcome",
    "concept_groups",
    "covered_names",
    "is_noise",
    "mine",
    "mine_candidates",
    "normalize",
    "specificity_score",
    "theme_names",
]
