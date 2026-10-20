"""Value types of the theme research steps (frozen, validated on construction)."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

type TargetKind = Literal["candidate", "existing"]
type Position = Literal["upstream", "midstream", "downstream", "related"]

POSITIONS: Final[tuple[Position, ...]] = ("upstream", "midstream", "downstream", "related")
DEFAULT_POSITION: Final[Position] = "related"
MAX_EXCERPT: Final = 200


class ThemeTarget(BaseModel):
    """A theme to research: a new-theme candidate or an existing concept theme (missing members).
    `names` are the label and its aliases, what an excerpt must contain to tie a page to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str = Field(min_length=1)
    kind: TargetKind
    label: str = Field(min_length=1)
    names: tuple[str, ...] = ()
    members: frozenset[str] = frozenset()


class ThemeClaim(BaseModel):
    """One raw, unverified statement: this page ties this listed company to this theme.

    `theme` is the concept theme key (kind existing) or the candidate id (kind candidate).
    `position` is the model's guess of the company's place in the chain; `published_on` is the
    page date when one could be read (ISO date), used for the staleness rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme: str = Field(min_length=1)
    kind: TargetKind
    label: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=20)
    excerpt: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)
    position: Position = DEFAULT_POSITION
    published_on: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        return (self.theme, self.symbol, self.url)

    @property
    def group_key(self) -> tuple[str, str]:
        return (self.theme, self.symbol)


class ResearchSummary(BaseModel):
    """Counts only. Safe to log, journal and print: it never carries page text or names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    targets: int = 0
    claims: int = 0
    duplicates: int = 0
    searches: int = 0
    pages: int = 0
    llm_calls: int = 0
    spend_usd: float = 0.0
    stopped_by: str | None = None
    dry_run: bool = False
    dropped: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, str] = Field(default_factory=dict)

    def line(self) -> str:
        dropped = ",".join(f"{k}:{v}" for k, v in sorted(self.dropped.items())) or "none"
        return (
            f"targets={self.targets} claims={self.claims} duplicates={self.duplicates} "
            f"searches={self.searches} pages={self.pages} llm_calls={self.llm_calls} "
            f"spend_usd={self.spend_usd:.3f} stopped_by={self.stopped_by or 'none'} "
            f"dropped=[{dropped}] {'would_write' if self.dry_run else 'written'}={self.claims}"
        )


class VerifySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: int = 0
    verified: int = 0
    candidates: int = 0
    rejected: int = 0
    skipped: int = 0
    member_rows: int = 0
    new_themes: int = 0
    pending_themes: int = 0
    covered: int = 0
    llm_calls: int = 0
    spend_usd: float = 0.0
    stopped_by: str | None = None
    dry_run: bool = False
    notes: list[str] = Field(default_factory=list)
    reasons: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, str] = Field(default_factory=dict)

    def line(self) -> str:
        reasons = ",".join(f"{k}:{v}" for k, v in sorted(self.reasons.items())) or "none"
        return (
            f"groups={self.groups} verified={self.verified} candidates={self.candidates} "
            f"rejected={self.rejected} skipped={self.skipped} member_rows={self.member_rows} "
            f"new_themes={self.new_themes} pending_themes={self.pending_themes} "
            f"covered={self.covered} "
            f"llm_calls={self.llm_calls} spend_usd={self.spend_usd:.3f} "
            f"stopped_by={self.stopped_by or 'none'} reasons=[{reasons}] "
            f"{'would_write' if self.dry_run else 'written'}={self.member_rows + self.new_themes}"
        )


__all__ = [
    "DEFAULT_POSITION",
    "MAX_EXCERPT",
    "POSITIONS",
    "Position",
    "ResearchSummary",
    "TargetKind",
    "ThemeClaim",
    "ThemeTarget",
    "VerifySummary",
]
