"""The contract of the operator's private concept-theme files.

`<reference dir>/themes/concepts/*.yaml`: one theme per file, a mapping::

    key: example-theme              # unique across files
    name_zh: 範例題材
    name_en: Example Theme          # optional
    description: one line           # optional
    related: [Other Theme, ...]     # optional display names of related themes
    members:
      - {symbol: "1234", name: Example Co, position: upstream, origin: seeded}

`position` is upstream | midstream | downstream | related; `origin` is seeded | curated |
proposed. The market is derived from the symbol (4-6 digit code = TW, ticker = US).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analytics.supply_chain.domain.concepts import ConceptOrigin, ConceptPosition


class ConceptMemberFileRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    name: str = ""
    position: ConceptPosition
    origin: ConceptOrigin = ConceptOrigin.SEEDED

    @field_validator("symbol", mode="before")
    @classmethod
    def _symbol_text(cls, value: object) -> object:
        return str(value) if isinstance(value, int) else value


class ConceptThemeFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    name_zh: str = Field(min_length=1)
    name_en: str = ""
    description: str = ""
    related: tuple[str, ...] = ()
    members: tuple[ConceptMemberFileRow, ...] = ()


__all__ = ["ConceptMemberFileRow", "ConceptThemeFile"]
