"""The contract of the operator's private entity files.

`<reference dir>/entities/catalog.yaml`: a YAML list of entities::

    - {id: Example Co, type: tw_company, mentions: 12, reports: 5, symbol: "1234",
       aliases: [1234_Example Co]}

`<reference dir>/entities/mentions.jsonl`: one JSON object per line::

    {"symbol": "1234", "entity": "Some Technology", "section": "business", "role": null,
     "count": 2}

`symbol` (catalog) is optional and is a Taiwan code or a US ticker. In the mention file it is the
Taiwan company whose report holds the mention.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analytics.supply_chain.domain.concepts import EntityType, MentionRole, MentionSection


def _text_symbol(value: object) -> object:
    return str(value) if isinstance(value, int) else value


class CatalogFileRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    type: EntityType
    mentions: int = Field(default=0, ge=0)
    reports: int = Field(default=0, ge=0)
    symbol: str | None = None
    aliases: tuple[str, ...] = ()

    @field_validator("symbol", mode="before")
    @classmethod
    def _symbol_text(cls, value: object) -> object:
        return _text_symbol(value)


class MentionFileRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    entity: str = Field(min_length=1)
    section: MentionSection
    role: MentionRole | None = None
    count: int = Field(default=1, ge=0)

    @field_validator("symbol", mode="before")
    @classmethod
    def _symbol_text(cls, value: object) -> object:
        return _text_symbol(value)


__all__ = ["CatalogFileRow", "MentionFileRow"]
