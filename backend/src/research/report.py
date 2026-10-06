"""Turn the model's draft into the report a reader sees - and enforce the citation rule doing it.

Governance (ADR-0014): every web-derived claim is cited. Here that is a hard filter, not advice to
the model: a claim keeps only the source ids that exist in the ledger *and* were actually read; a
claim left with none is dropped and counted; a section left with no claims disappears. Nothing the
model says without a real source can reach the output. The surviving text is then passed through the
governance output guard (no buy/sell, price targets, position sizing).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from research.ledger import SourceLedger
from research.models import (
    Claim,
    DraftReport,
    FinalReport,
    ReportClaim,
    ReportSection,
    ReportSource,
    StopReason,
)

TextGuard = Callable[[str], str]


def _cite(claim: Claim, ledger: SourceLedger, guard: TextGuard) -> tuple[ReportClaim | None, bool]:
    """(the claim as it may be shown or None, whether it was dropped for lacking a source)."""
    valid: list[int] = []
    for source_id in claim.source_ids:
        source = ledger.get(source_id)
        if source is not None and source.citeable and source_id not in valid:
            valid.append(source_id)
    if not valid:
        return None, True
    text = guard(claim.text.strip())
    if not text:
        return None, False
    return ReportClaim(text=text, source_ids=tuple(sorted(valid))), False


def build_report(  # noqa: PLR0913 - one argument per fact the report records about the run
    *,
    question: str,
    draft: DraftReport,
    ledger: SourceLedger,
    guard: TextGuard,
    as_of: date,
    stopped_because: StopReason,
    rounds: int,
    searches: int,
    pages_read: int,
) -> FinalReport:
    dropped = 0

    def keep(claims: list[Claim]) -> tuple[ReportClaim, ...]:
        nonlocal dropped
        kept: list[ReportClaim] = []
        for claim in claims:
            cited, was_dropped = _cite(claim, ledger, guard)
            dropped += int(was_dropped)
            if cited is not None:
                kept.append(cited)
        return tuple(kept)

    summary = keep(draft.summary)
    sections = tuple(
        ReportSection(heading=guard(section.heading.strip()), claims=claims)
        for section in draft.sections
        if (claims := keep(section.claims))
    )
    cited_ids = {
        source_id
        for claim in (*summary, *(c for s in sections for c in s.claims))
        for source_id in claim.source_ids
    }
    sources = tuple(
        ReportSource(id=s.id, title=s.title, url=s.url, published=s.published)
        for s in ledger.citeable()
        if s.id in cited_ids
    )
    has_content = bool(summary or sections)
    return FinalReport(
        question=question,
        title=guard(draft.title.strip()) or question,
        as_of=as_of,
        summary=summary,
        sections=sections,
        open_questions=tuple(guard(q.strip()) for q in draft.open_questions if q.strip()),
        sources=sources,
        stopped_because=stopped_because if has_content else "no_sources",
        dropped_claims=dropped,
        rounds=rounds,
        searches=searches,
        pages_read=pages_read,
    )


__all__ = ["TextGuard", "build_report"]
